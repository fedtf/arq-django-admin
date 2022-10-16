import asyncio
from dataclasses import dataclass
from typing import List, Optional, Set

from arq import ArqRedis
from arq.connections import RedisSettings
from arq.constants import result_key_prefix
from arq.jobs import DeserializationError, Job as ArqJob, JobDef, JobStatus
from django.utils import timezone

from arq_admin import settings
from arq_admin.job import JobInfo
from arq_admin.redis import get_redis


@dataclass
class QueueStats:
    name: str
    host: str
    port: int
    database: int

    queued_jobs: Optional[int] = None
    running_jobs: Optional[int] = None
    deferred_jobs: Optional[int] = None

    error: Optional[str] = None


@dataclass
class Queue:
    redis_settings: RedisSettings
    name: str

    @classmethod
    def from_name(cls, name: str) -> 'Queue':
        return cls(
            name=name,
            redis_settings=settings.ARQ_QUEUES[name],
        )

    async def get_jobs(self, status: Optional[JobStatus] = None) -> List[JobInfo]:
        async with get_redis(self.redis_settings) as redis:
            job_ids = await self._get_job_ids(redis)

            if status:
                job_ids_tuple = tuple(job_ids)
                statuses = await asyncio.gather(*[self._get_job_status(job_id, redis) for job_id in job_ids_tuple])
                job_ids = {job_id for (job_id, job_status) in zip(job_ids_tuple, statuses) if job_status == status}

            jobs: List[JobInfo] = await asyncio.gather(*[self.get_job_by_id(job_id, redis) for job_id in job_ids])

        return jobs

    async def get_stats(self) -> QueueStats:
        result = QueueStats(
            name=self.name,
            host=str(self.redis_settings.host),
            port=self.redis_settings.port,
            database=self.redis_settings.database,
        )
        try:
            async with get_redis(self.redis_settings) as redis:
                job_ids = await self._get_job_ids(redis)
                statuses = await asyncio.gather(*[self._get_job_status(job_id, redis) for job_id in job_ids])
        except Exception as ex:  # noqa: B902
            result.error = str(ex)
        else:
            result.queued_jobs = len([status for status in statuses if status == JobStatus.queued])
            result.running_jobs = len([status for status in statuses if status == JobStatus.in_progress])
            result.deferred_jobs = len([status for status in statuses if status == JobStatus.deferred])

        return result

    async def get_job_by_id(self, job_id: str, redis: Optional[ArqRedis] = None) -> JobInfo:
        if redis is None:
            async with get_redis(self.redis_settings) as redis:
                return await self._get_job_by_id(job_id, redis)
        return await self._get_job_by_id(job_id, redis)

    async def _get_job_by_id(self, job_id: str, redis: ArqRedis) -> JobInfo:
        arq_job = ArqJob(
            job_id=job_id,
            redis=redis,
            _queue_name=self.name,
            _deserializer=settings.ARQ_DESERIALIZER_BY_QUEUE.get(self.name),
        )

        unknown_function_msg = "Can't find job"
        base_info = None
        try:
            base_info = await arq_job.info()
        except DeserializationError:
            unknown_function_msg = "Unknown, can't deserialize"

        if not base_info:
            base_info = JobDef(
                function=unknown_function_msg,
                args=(),
                kwargs={},
                job_try=-1,
                enqueue_time=timezone.now().replace(year=2077),
                score=420,
            )

        job_info = JobInfo.from_base(base_info, job_id)
        job_info.status = await arq_job.status()

        return job_info

    async def _get_job_status(self, job_id: str, redis: ArqRedis) -> JobStatus:
        arq_job = ArqJob(
            job_id=job_id,
            redis=redis,
            _queue_name=self.name,
            _deserializer=settings.ARQ_DESERIALIZER_BY_QUEUE.get(self.name),
        )
        return await arq_job.status()

    async def _get_job_ids(self, redis: ArqRedis) -> Set[str]:
        raw_job_ids = set(await redis.zrangebyscore(self.name, '-inf', 'inf'))
        result_keys = await redis.keys(f'{result_key_prefix}*')
        raw_job_ids |= {key[len(result_key_prefix):] for key in result_keys}

        return {job_id.decode('utf-8') if isinstance(job_id, bytes) else job_id for job_id in raw_job_ids}
