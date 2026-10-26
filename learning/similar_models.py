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


class TaskVector(models.Model):
    """A task's text and its sentence vector (learning/embeddings.py); re-embedded when the text changes."""
    task = models.OneToOneField('learning.Task', on_delete=models.CASCADE, primary_key=True, related_name='text_vector')
    source_text = models.TextField(blank=True, default='')  # words of a pictures-only condition, from its source bank
    digest = models.CharField(max_length=40, blank=True, default='')
    vector = models.BinaryField(null=True, blank=True)  # 384 × float16
    updated_at = models.DateTimeField(auto_now=True)
