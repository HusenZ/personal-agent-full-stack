from app.core.exceptions import NotFoundError


class ConversationNotFoundError(NotFoundError):
    code = "CONVERSATION_NOT_FOUND"
    # Same error for missing and foreign conversations, so existence isn't leaked.
    message = "Conversation was not found."
