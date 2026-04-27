from django.db import models
from django.contrib.auth.models import User

class BiasAnalysis(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    title = models.CharField(max_length=200)
    dataset_name = models.CharField(max_length=200)
    analysis_type = models.CharField(max_length=100)
    
    # Fairness Metrics
    demographic_parity = models.FloatField(null=True)
    equalized_odds = models.FloatField(null=True)
    disparate_impact = models.FloatField(null=True)
    
    # Results
    bias_detected = models.BooleanField(default=False)
    recommendations = models.JSONField(default=list)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'bias_analysis'

class ChatSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    title = models.CharField(max_length=200)
    messages = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'chat_sessions'

class Dataset(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    file_path = models.CharField(max_length=500)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'datasets'
