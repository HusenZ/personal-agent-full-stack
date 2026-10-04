import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import '../auth/token_store.dart';
import 'api_exception.dart';

/// A successful response: the envelope's `data`, and its `meta`.
class ApiResponse {
  const ApiResponse(this.data, this.meta);

  final Object? data;
  final Map<String, dynamic> meta;

  Map<String, dynamic> get object => data! as Map<String, dynamic>;
  List<dynamic> get list => data! as List<dynamic>;
}

/// Talks to the backend: JSON envelopes, bearer tokens, and their renewal.
///
/// Refresh tokens rotate, and the server treats a refresh token used twice as
/// stolen and revokes the whole session. So renewal is single-flight: however
/// many requests find the access token expired at once, one refresh is sent
/// and every request waits for it.
class ApiClient {
  ApiClient({
    required this.baseUrl,
    required http.Client httpClient,
    required TokenStore tokens,
    DateTime Function()? clock,
    this.requestTimeout = const Duration(seconds: 30),
  }) : _http = httpClient,
       _tokenStore = tokens,
       _clock = clock ?? DateTime.now;

  final Uri baseUrl;
  final Duration requestTimeout;
  final http.Client _http;
  final TokenStore _tokenStore;
  final DateTime Function() _clock;

  /// Called when the session can no longer be renewed, so the app can return
  /// to sign-in. Not called for network failures: being offline is not being
  /// signed out.
  void Function()? onSessionEnded;

  Future<TokenPair?>? _refreshing;

  static const _expiryMargin = Duration(seconds: 30);

  Future<ApiResponse> get(String path, {Map<String, String>? query}) => send('GET', path, query: query);

  Future<ApiResponse> post(String path, {Object? body, bool authenticated = true}) =>
      send('POST', path, body: body, authenticated: authenticated);

  Future<ApiResponse> patch(String path, {Object? body}) => send('PATCH', path, body: body);

  Future<ApiResponse> delete(String path) => send('DELETE', path);

  Future<ApiResponse> send(
    String method,
    String path, {
    Object? body,
    Map<String, String>? query,
    bool authenticated = true,
  }) async {
    final response = await _withAuthentication(
      authenticated,
      (token) => _sendOnce(method, path, body: body, query: query, token: token),
    );
    return _decode(response);
  }

  /// Opens a server-sent event stream. Errors before the stream starts are
  /// thrown like any other request's; the caller owns the returned stream.
  Future<http.StreamedResponse> openEventStream(String path, {required Object body}) async {
    final response = await _withAuthentication(true, (token) async {
      final request = http.Request('POST', _uri(path))
        ..headers.addAll(_headers(token))
        ..headers['accept'] = 'text/event-stream'
        ..body = jsonEncode(body);
      final streamed = await _guard(_http.send(request));
      if (streamed.statusCode == 200) {
        return _Outcome.stream(streamed);
      }
      return _Outcome.response(await http.Response.fromStream(streamed));
    });
    final stream = response.stream;
    if (stream == null) {
      throw _error(response.response!);
    }
    return stream;
  }

  /// Exchanges credentials for tokens and stores them.
  Future<void> signIn(String email, String password) async {
    final response = await post(
      '/api/v1/auth/login',
      body: {'email': email, 'password': password},
      authenticated: false,
    );
    await _tokenStore.write(TokenPair.fromJson(response.object));
  }

  /// Revokes the refresh token server-side, best effort, and forgets the session.
  Future<void> signOut() async {
    final tokens = await _tokenStore.read();
    await _tokenStore.clear();
    if (tokens == null) {
      return;
    }
    try {
      await post('/api/v1/auth/logout', body: {'refresh_token': tokens.refreshToken}, authenticated: false);
    } on ApiException {
      // Already cleared locally; the server token will just expire.
    }
  }

  Future<bool> hasSession() async => await _tokenStore.read() != null;

  Future<_Outcome> _withAuthentication(bool authenticated, Future<_Outcome> Function(String? token) attempt) async {
    if (!authenticated) {
      return attempt(null);
    }

    var tokens = await _tokenStore.read();
    if (tokens == null) {
      _endSession();
      throw const ApiException(statusCode: 401, code: 'UNAUTHENTICATED', message: 'Not signed in.');
    }
    if (tokens.expiresWithin(_expiryMargin, _clock())) {
      tokens = await _refresh(tokens.refreshToken);
    }

    final outcome = await attempt(tokens.accessToken);
    if (outcome.statusCode != 401) {
      return outcome;
    }

    // Refresh once and retry; a second rejection is final.
    final renewed = await _refresh(tokens.refreshToken);
    final retried = await attempt(renewed.accessToken);
    if (retried.statusCode == 401) {
      await _tokenStore.clear();
      _endSession();
    }
    return retried;
  }

  Future<TokenPair> _refresh(String refreshToken) async {
    final inFlight = _refreshing ??= _performRefresh(refreshToken);
    try {
      final tokens = await inFlight;
      if (tokens == null) {
        throw const ApiException(
          statusCode: 401,
          code: 'INVALID_REFRESH_TOKEN',
          message: 'The session could not be renewed.',
        );
      }
      return tokens;
    } finally {
      if (identical(_refreshing, inFlight)) {
        _refreshing = null;
      }
    }
  }

  /// Null when the server refused the refresh token: the session is over.
  /// Network failures are thrown instead, leaving the session intact.
  Future<TokenPair?> _performRefresh(String refreshToken) async {
    final current = await _tokenStore.read();
    // Reuse a refresh already in flight instead of a spent token.
    if (current != null && current.refreshToken != refreshToken && !current.expiresWithin(_expiryMargin, _clock())) {
      return current;
    }

    final outcome = await _sendOnce('POST', '/api/v1/auth/refresh', body: {'refresh_token': refreshToken});
    final response = outcome.response!;
    if (response.statusCode == 200) {
      final tokens = TokenPair.fromJson(_decode(outcome).object);
      await _tokenStore.write(tokens);
      return tokens;
    }
    if (response.statusCode == 401) {
      await _tokenStore.clear();
      _endSession();
      return null;
    }
    throw _error(response);
  }

  void _endSession() => onSessionEnded?.call();

  Future<_Outcome> _sendOnce(
    String method,
    String path, {
    Object? body,
    Map<String, String>? query,
    String? token,
  }) async {
    final request = http.Request(method, _uri(path, query))..headers.addAll(_headers(token));
    if (body != null) {
      request.body = jsonEncode(body);
    }
    final streamed = await _guard(_http.send(request));
    final response = await _guard(http.Response.fromStream(streamed));
    return _Outcome.response(response);
  }

  Future<T> _guard<T>(Future<T> future) async {
    try {
      return await future.timeout(requestTimeout);
    } on TimeoutException {
      throw const ApiException.network('The server took too long to respond.');
    } on SocketException catch (error) {
      throw ApiException.network(error.message);
    } on http.ClientException catch (error) {
      throw ApiException.network(error.message);
    }
  }

  Uri _uri(String path, [Map<String, String>? query]) => baseUrl.replace(
    path: '${baseUrl.path.replaceAll(RegExp(r'/$'), '')}$path',
    queryParameters: (query == null || query.isEmpty) ? null : query,
  );

  Map<String, String> _headers(String? token) => {
    'content-type': 'application/json',
    'accept': 'application/json',
    if (token != null) 'authorization': 'Bearer $token',
  };

  ApiResponse _decode(_Outcome outcome) {
    final response = outcome.response!;
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw _error(response);
    }
    if (response.statusCode == 204 || response.bodyBytes.isEmpty) {
      return const ApiResponse(null, {});
    }
    final json = jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
    return ApiResponse(json['data'], (json['meta'] as Map<String, dynamic>?) ?? const {});
  }

  ApiException _error(http.Response response) {
    var code = 'HTTP_${response.statusCode}';
    var message = response.reasonPhrase ?? 'Request failed';
    String? requestId = response.headers['x-request-id'];
    try {
      final json = jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
      final error = json['error'] as Map<String, dynamic>;
      code = error['code'] as String;
      message = error['message'] as String;
      requestId = ((json['meta'] as Map<String, dynamic>?)?['request_id'] as String?) ?? requestId;
    } on Object {
      // Not our envelope (a proxy's error page, say): keep the HTTP status.
    }
    final retryAfter = int.tryParse(response.headers['retry-after'] ?? '');
    return ApiException(
      statusCode: response.statusCode,
      code: code,
      message: message,
      requestId: requestId,
      retryAfter: retryAfter == null ? null : Duration(seconds: retryAfter),
    );
  }
}

/// What one attempt produced: a buffered response, or an open event stream.
class _Outcome {
  _Outcome.response(http.Response this.response) : stream = null;
  _Outcome.stream(http.StreamedResponse this.stream) : response = null;

  final http.Response? response;
  final http.StreamedResponse? stream;

  int get statusCode => response?.statusCode ?? stream!.statusCode;
}
