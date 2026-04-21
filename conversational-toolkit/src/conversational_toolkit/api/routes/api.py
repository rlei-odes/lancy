from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse


from conversational_toolkit.api.auth.base import AuthProvider
from conversational_toolkit.conversation_database.controller import (
    ConversationalToolkitController,
    ClientMessage,
    MessageInput,
    ClientConversation,
    ConversationInput,
    ReactionInput,
)
from conversational_toolkit.conversation_database.data_models.conversation import Conversation
from conversational_toolkit.conversation_database.data_models.reaction import Reaction


def create_api_router(
    controller: ConversationalToolkitController,
    auth_provider: AuthProvider,
    conversation_metadata_provider=None,  # () -> dict  — injects kb_id/kb_name/rag_config_snapshot
) -> APIRouter:
    api_router = APIRouter(prefix="/api/v1")

    def _get_extra_meta() -> dict:
        return conversation_metadata_provider() if conversation_metadata_provider else {}

    @api_router.post("/messages")
    async def post_user_message(
        user_input: MessageInput, user_id: str = Depends(auth_provider.get_current_user_id)
    ) -> ClientMessage:
        conversation_id = user_input.conversation_id
        if conversation_id:
            conversation = await controller.conversation_db.get_conversation_by_id(conversation_id)
            if conversation.user_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="User doesn't have access to this conversation"
                )
        return await controller.process_new_message(
            user_id=user_id, user_input=user_input, extra_meta=_get_extra_meta()
        )

    @api_router.post("/messages/stream")
    async def post_user_message_stream(
        user_input: MessageInput, user_id: str = Depends(auth_provider.get_current_user_id)
    ) -> StreamingResponse:
        conversation_id = user_input.conversation_id
        if conversation_id:
            conversation = await controller.conversation_db.get_conversation_by_id(conversation_id)
            if conversation.user_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="User doesn't have access to this conversation"
                )
        return StreamingResponse(
            controller.process_new_message_stream(user_id=user_id, user_input=user_input, extra_meta=_get_extra_meta()),  # type: ignore
            media_type="json/event-stream",
        )

    @api_router.get("/conversations")
    async def get_conversations_metadata(
        user_id: str = Depends(auth_provider.get_current_user_id),
    ) -> list[Conversation]:
        return await controller.get_conversations_data_by_user_id(user_id)

    @api_router.get("/conversations/{conversation_id}")
    async def get_conversation(
        conversation_id: str, user_id: str = Depends(auth_provider.get_current_user_id)
    ) -> ClientConversation:
        return await controller.get_conversation_by_id(conversation_id)

    @api_router.put("/conversations/{conversation_id}")
    async def update_conversation(
        conversation_id: str,
        conversation_updates: ConversationInput,
        user_id: str = Depends(auth_provider.get_current_user_id),
    ) -> Conversation:
        return await controller.update_conversation(conversation_id, conversation_updates)

    @api_router.delete("/conversations")
    async def delete_all_conversations(
        user_id: str = Depends(auth_provider.get_current_user_id),
    ) -> int:
        conversations = await controller.get_conversations_data_by_user_id(user_id)
        count = 0
        for conv in conversations:
            await controller.delete_conversation(conv.id)
            count += 1
        return count

    @api_router.delete("/conversations/{conversation_id}")
    async def delete_conversation(
        conversation_id: str, user_id: str = Depends(auth_provider.get_current_user_id)
    ) -> bool:
        return await controller.delete_conversation(conversation_id)

    @api_router.get("/conversations/{conversation_id}/messages")
    async def get_messages(
        conversation_id: str, user_id: str = Depends(auth_provider.get_current_user_id)
    ) -> list[ClientMessage]:
        return await controller.get_messages_by_conversation_id(conversation_id)

    @api_router.post("/conversations/{conversation_id}/messages/{message_id}/reactions")
    async def post_reaction(
        reaction_input: ReactionInput,
        message_id: str,
        user_id: str = Depends(auth_provider.get_current_user_id),
    ) -> Reaction:
        return await controller.add_reaction(reaction_input=reaction_input, message_id=message_id, user_id=user_id)

    return api_router
