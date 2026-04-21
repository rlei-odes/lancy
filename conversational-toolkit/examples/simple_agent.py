from conversational_toolkit.agents.tool_agent import ToolAgent
from conversational_toolkit.api.server import create_app
from conversational_toolkit.conversation_database.controller import ConversationalToolkitController
from conversational_toolkit.conversation_database.in_memory.conversation import InMemoryConversationDatabase
from conversational_toolkit.conversation_database.in_memory.message import InMemoryMessageDatabase
from conversational_toolkit.conversation_database.in_memory.reactions import InMemoryReactionDatabase
from conversational_toolkit.conversation_database.in_memory.source import InMemorySourceDatabase
from conversational_toolkit.conversation_database.in_memory.user import InMemoryUserDatabase
from conversational_toolkit.embeddings.sentence_transformer import SentenceTransformerEmbeddings
from conversational_toolkit.llms.ollama import OllamaLLM
from conversational_toolkit.retriever.vectorstore_retriever import VectorStoreRetriever
from conversational_toolkit.tools.retriever import RetrieverTool
from conversational_toolkit.vectorstores.chromadb import ChromaDBVectorStore

embedding_model = SentenceTransformerEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

conversation_database = InMemoryConversationDatabase("conversations.json")
message_database = InMemoryMessageDatabase("messages.json")
reaction_database = InMemoryReactionDatabase("reactions.json")
source_database = InMemorySourceDatabase("sources.json")
user_database = InMemoryUserDatabase("users.json")
vector_store = ChromaDBVectorStore("chunks.db")

agent = ToolAgent(
    llm=OllamaLLM(
        tools=[
            RetrieverTool(
                name="Retriever",
                description="Retriever is a tool that retrieves information from a database.",
                retriever=VectorStoreRetriever(embedding_model, vector_store, 5),
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                llm=OllamaLLM(),
            )
        ]
    ),
    system_prompt="You are a helpful AI assistant specialized in answering question.",
    max_steps=5,
)

controller = ConversationalToolkitController(
    conversation_database,
    message_database,
    reaction_database,
    source_database,
    user_database,
    agent,
)

app = create_app(controller)
