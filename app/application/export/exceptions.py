from app.core.exceptions import ServiceUnavailableError


class ExportUnavailableError(ServiceUnavailableError):
    code = "EXPORT_UNAVAILABLE"
    message = "Your data export could not be prepared right now. Please try again shortly."
