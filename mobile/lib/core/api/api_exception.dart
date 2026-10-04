/// A failed request, carrying what the backend's error envelope said.
///
/// The backend answers every error with a stable `code`; the app decides what
/// to show from that code, never from the free-text message.
class ApiException implements Exception {
  const ApiException({
    required this.statusCode,
    required this.code,
    required this.message,
    this.requestId,
    this.retryAfter,
  });

  /// No response arrived: offline, DNS failure, connection refused, timeout.
  const ApiException.network(this.message)
    : statusCode = 0,
      code = 'NETWORK_ERROR',
      requestId = null,
      retryAfter = null;

  final int statusCode;
  final String code;
  final String message;

  /// Quoted when reporting a problem, so it can be found in the server logs.
  final String? requestId;
  final Duration? retryAfter;

  bool get isNetwork => statusCode == 0;
  bool get isUnauthenticated => statusCode == 401;

  /// Text fit to show a person. Codes the app knows get specific wording;
  /// anything else gets a generic message rather than raw server text.
  String get userMessage => switch (code) {
    'NETWORK_ERROR' => "Can't reach the server. Check your connection and try again.",
    'INVALID_CREDENTIALS' => 'That email and password do not match.',
    'EMAIL_ALREADY_REGISTERED' => 'An account with that email already exists.',
    'INACTIVE_USER' => 'This account has been deactivated.',
    'INCORRECT_PASSWORD' => 'That password is not right.',
    'CONVERSATION_NOT_FOUND' => 'That conversation no longer exists.',
    'PLAN_UNAVAILABLE' => "I couldn't make a plan right now. Please try again in a moment.",
    'PLAN_NOT_FOUND' || 'PLAN_BLOCK_NOT_FOUND' => 'That plan has changed. Pull to refresh.',
    'MEMORY_NOT_FOUND' => 'That memory no longer exists.',
    'RATE_LIMITED' =>
      retryAfter == null
          ? 'Too many attempts. Please wait a moment.'
          : 'Too many attempts. Try again in ${_describe(retryAfter!)}.',
    'VALIDATION_ERROR' => 'Some details are not valid. Check them and try again.',
    'PAYLOAD_TOO_LARGE' => 'That is too long to send.',
    'INSUFFICIENT_CONTEXT' => 'There is not enough known about you yet. Finish onboarding first.',
    'UNAUTHENTICATED' || 'INVALID_REFRESH_TOKEN' => 'Your session has ended. Please sign in again.',
    _ =>
      statusCode >= 500
          ? 'Something went wrong on our side. Please try again.'
          : 'That did not work. Please try again.',
  };

  static String _describe(Duration wait) =>
      wait.inMinutes >= 1 ? '${wait.inMinutes} min' : '${wait.inSeconds.clamp(1, 59)} s';

  @override
  String toString() => 'ApiException($statusCode, $code, request $requestId)';
}
