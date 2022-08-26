import asyncio
from operator import attrgetter
from typing import Any, Dict, List, Optional

from arq.jobs import JobStatus
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.utils.decorators import method_decorator
from django.views.generic import DetailView, ListView

from arq_admin.job import JobInfo
from arq_admin.queue import Queue, QueueStats
from arq_admin.settings import ARQ_QUEUES


@method_decorator(staff_member_required, name='dispatch')
class QueueListView(ListView):
    template_name = 'arq_admin/queues.html'

    def get_queryset(self) -> List[QueueStats]:
        async def _gather_queues() -> List[QueueStats]:
            tasks = [Queue.from_name(name).get_stats() for name in ARQ_QUEUES.keys()]

            return await asyncio.gather(*tasks)

        return asyncio.run(_gather_queues())

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(admin.site.each_context(self.request))

        return context


@method_decorator(staff_member_required, name='dispatch')
class BaseJobListView(ListView):
    paginate_by = 100
    template_name = 'arq_admin/jobs.html'

    status: Optional[JobStatus] = None
    job_status_label: Optional[str] = None

    @property
    def job_status(self) -> str:
        if self.job_status_label:
            return self.job_status_label

        return self.status.value.capitalize() if self.status else 'Unknown'

    def get_queryset(self) -> List[JobInfo]:
        queue_name = self.kwargs['queue_name']  # pragma: no cover  # looks like a pytest-cov bug coz the rows below
        queue = Queue.from_name(queue_name)  # pragma: no cover     # are covered
        jobs = asyncio.run(queue.get_jobs(status=self.status))
        return sorted(jobs, key=attrgetter('enqueue_time'))

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update({
            **admin.site.each_context(self.request),
            'queue_name': self.kwargs['queue_name'],
            'job_status': self.job_status,
        })

        return context


class AllJobListView(BaseJobListView):
    job_status_label = 'All'


class QueuedJobListView(BaseJobListView):
    status = JobStatus.queued


class RunningJobListView(BaseJobListView):
    status = JobStatus.in_progress


class DeferredJobListView(BaseJobListView):
    status = JobStatus.deferred


class JobDetailView(DetailView):
    template_name = 'arq_admin/job_detail.html'

    def get_object(self, queryset: Optional[Any] = None) -> JobInfo:
        queue = Queue.from_name(self.kwargs['queue_name'])  # pragma: no cover
        return asyncio.run(queue.get_job_by_id(self.kwargs['job_id']))

    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context['queue_name'] = self.kwargs['queue_name']

        return context
