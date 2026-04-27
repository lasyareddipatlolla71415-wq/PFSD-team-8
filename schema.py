import graphene
from graphene_django import DjangoObjectType
from graphene_file_upload.scalars import Upload
import graphql_jwt
from django.contrib.auth.models import User
from .models import BiasAnalysis, ChatSession, Dataset
from ml_models.fairness_analyzer import FairnessAnalyzer
try:
    from ml_models.tinyllama_service import generate as tinyllama_generate
    TINYLLAMA_AVAILABLE = True
except Exception:
    TINYLLAMA_AVAILABLE = False
    tinyllama_generate = None
from graph_analysis.bias_graph import BiasGraphAnalyzer
import google.generativeai as genai
from django.conf import settings
from django.core.files.storage import default_storage
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mongo_db as mdb

genai.configure(api_key=settings.GEMINI_API_KEY)

class UserType(DjangoObjectType):
    class Meta:
        model = User
        fields = ('id', 'username', 'email')

class BiasAnalysisType(DjangoObjectType):
    class Meta:
        model = BiasAnalysis
        fields = '__all__'

class ChatSessionType(DjangoObjectType):
    class Meta:
        model = ChatSession
        fields = '__all__'

class DatasetType(DjangoObjectType):
    class Meta:
        model = Dataset
        fields = '__all__'

class Query(graphene.ObjectType):
    me = graphene.Field(UserType)
    all_analyses = graphene.List(BiasAnalysisType)
    analysis_by_id = graphene.Field(BiasAnalysisType, id=graphene.String())
    chat_sessions = graphene.List(ChatSessionType)
    datasets = graphene.List(DatasetType)
    
    def resolve_me(self, info):
        user = info.context.user
        if user.is_authenticated:
            return user
        return None
    
    def resolve_all_analyses(self, info):
        return BiasAnalysis.objects.all()
    
    def resolve_analysis_by_id(self, info, id):
        return BiasAnalysis.objects.get(id=id)
    
    def resolve_chat_sessions(self, info):
        user = info.context.user
        if user.is_authenticated:
            return ChatSession.objects.filter(user=user)
        return ChatSession.objects.filter(user=None)
    
    def resolve_datasets(self, info):
        user = info.context.user
        if user.is_authenticated:
            return Dataset.objects.filter(user=user)
        return []

class RegisterUser(graphene.Mutation):
    class Arguments:
        username = graphene.String(required=True)
        email = graphene.String(required=True)
        password = graphene.String(required=True)
    
    user = graphene.Field(UserType)
    token = graphene.String()
    
    def mutate(self, info, username, email, password):
        user = User.objects.create_user(username=username, email=email, password=password)
        token = graphql_jwt.shortcuts.get_token(user)
        return RegisterUser(user=user, token=token)

class UploadDataset(graphene.Mutation):
    class Arguments:
        file = Upload(required=True)
        name = graphene.String(required=True)
    
    dataset = graphene.Field(DatasetType)
    
    def mutate(self, info, file, name):
        user = info.context.user
        if not user.is_authenticated:
            raise Exception("Authentication required")
        
        # Save file
        file_path = default_storage.save(f'datasets/{file.name}', file)
        full_path = os.path.join(settings.MEDIA_ROOT, file_path)
        
        dataset = Dataset.objects.create(
            user=user,
            name=name,
            file_path=full_path,
            metadata={'original_name': file.name}
        )
        return UploadDataset(dataset=dataset)

class AnalyzeDataset(graphene.Mutation):
    class Arguments:
        dataset_id = graphene.String(required=True)
        protected_attribute = graphene.String(required=True)
    
    analysis = graphene.Field(BiasAnalysisType)
    
    def mutate(self, info, dataset_id, protected_attribute):
        dataset = Dataset.objects.get(id=dataset_id)
        analyzer = FairnessAnalyzer()
        results = analyzer.analyze(dataset.file_path, protected_attribute)
        
        user = info.context.user if info.context.user.is_authenticated else None
        
        analysis = BiasAnalysis.objects.create(
            user=user,
            title=f"Analysis: {dataset.name}",
            dataset_name=dataset.name,
            analysis_type="fairness_metrics",
            demographic_parity=results['demographic_parity'],
            equalized_odds=results['equalized_odds'],
            disparate_impact=results['disparate_impact'],
            bias_detected=results['bias_detected'],
            recommendations=results['recommendations']
        )
        return AnalyzeDataset(analysis=analysis)

class SendChatMessage(graphene.Mutation):
    class Arguments:
        session_id = graphene.String()
        message = graphene.String(required=True)

    response = graphene.String()
    session_id = graphene.String()

    def mutate(self, info, message, session_id=None):
        if not session_id:
            new_s = mdb.create_session()
            session_id = new_s['id']

        model = genai.GenerativeModel('gemini-2.0-flash', system_instruction="""You are Smart Fairness Analyzer, a specialized assistant for Fairness and Bias Testing in AI systems.
GOAL: Help identify, measure, and mitigate algorithmic bias using Equalized Odds, Demographic Parity, and Counterfactual Fairness.""")
        response = model.generate_content(message)
        response_text = response.text

        mdb.add_message(session_id, message, response_text)
        return SendChatMessage(response=response_text, session_id=session_id)

class SendTinyLlamaMessage(graphene.Mutation):
    class Arguments:
        message = graphene.String(required=True)

    response = graphene.String()

    def mutate(self, info, message):
        if not TINYLLAMA_AVAILABLE:
            raise Exception("TinyLlama unavailable: PyTorch failed to load.")
        response = tinyllama_generate(message)
        return SendTinyLlamaMessage(response=response)


class AnalyzeBiasGraph(graphene.Mutation):
    class Arguments:
        dataset_id = graphene.String(required=True)
    
    graph_data = graphene.JSONString()
    
    def mutate(self, info, dataset_id):
        dataset = Dataset.objects.get(id=dataset_id)
        graph_analyzer = BiasGraphAnalyzer()
        graph_data = graph_analyzer.create_bias_graph(dataset.file_path)
        return AnalyzeBiasGraph(graph_data=graph_data)

class Mutation(graphene.ObjectType):
    token_auth = graphql_jwt.ObtainJSONWebToken.Field()
    verify_token = graphql_jwt.Verify.Field()
    refresh_token = graphql_jwt.Refresh.Field()
    register_user = RegisterUser.Field()
    
    upload_dataset = UploadDataset.Field()
    analyze_dataset = AnalyzeDataset.Field()
    send_chat_message = SendChatMessage.Field()
    analyze_bias_graph = AnalyzeBiasGraph.Field()
    send_tiny_llama_message = SendTinyLlamaMessage.Field()

schema = graphene.Schema(query=Query, mutation=Mutation)
