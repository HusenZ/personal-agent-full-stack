from app.core.exceptions import NotFoundError


class ProfileNotFoundError(NotFoundError):
    code = "PROFILE_NOT_FOUND"
    message = "Profile was not found."
