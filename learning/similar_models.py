"""Similar tasks for the "Похожие" button, found by a neural text model (deploy/build_similar_tasks.py)."""
from django.db import models


class TaskSimilarity(models.Model):
    task = models.ForeignKey('learning.Task', on_delete=models.CASCADE, related_name='similar_links')
    other = models.ForeignKey('learning.Task', on_delete=models.CASCADE, related_name='+')
    score = models.FloatField()
    rank = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ['rank']
        constraints = [models.UniqueConstraint(fields=['task', 'other'], name='unique_task_similarity')]
