import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:personal_intelligence/core/api/api_client.dart';
import 'package:personal_intelligence/core/api/api_exception.dart';
import 'package:personal_intelligence/core/auth/token_store.dart';

final now = DateTime.utc(2026, 10, 2, 12);

TokenPair tokens(String suffix, {Duration validFor = const Duration(minutes: 15)}) =>
    TokenPair(accessToken: 'access-$suffix', refreshToken: 'refresh-$suffix', accessExpiresAt: now.add(validFor));

http.Response json(Object body, {int status = 200, Map<String, String> headers = const {}}) =>
    http.Response(jsonEncode(body), status, headers: {'content-type': 'application/json', ...headers});

Map<String, dynamic> pairJson(String suffix) => {
  'access_token': 'access-$suffix',
  'refresh_token': 'refresh-$suffix',
  'token_type': 'bearer',
  'access_token_expires_at': now.add(const Duration(minutes: 15)).toIso8601String(),
  'refresh_token_expires_at': now.add(const Duration(days: 30)).toIso8601String(),
};

http.Response unauthenticated() => json({
  'error': {'code': 'UNAUTHENTICATED', 'message': 'Token expired.'},
  'meta': {'request_id': 'req-1'},
}, status: 401);

ApiClient client(MockClient http, MemoryTokenStore store) =>
    ApiClient(baseUrl: Uri.parse('https://api.test'), httpClient: http, tokens: store, clock: () => now);

void main() {
  group('responses', () {
    test('the envelope is unwrapped into data and meta', () async {
      final api = client(
        MockClient(
          (_) async => json({
            'data': {'a': 1},
            'meta': {'request_id': 'r'},
          }),
        ),
        MemoryTokenStore(tokens('1')),
      );

      final response = await api.get('/x');

      expect(response.object, {'a': 1});
      expect(response.meta['request_id'], 'r');
    });

    test('errors carry the code, request id and wait from the server', () async {
      final api = client(
        MockClient(
          (_) async => json(
            {
              'error': {'code': 'RATE_LIMITED', 'message': 'Slow down.'},
              'meta': {'request_id': 'req-9'},
            },
            status: 429,
            headers: {'retry-after': '42'},
          ),
        ),
        MemoryTokenStore(tokens('1')),
      );

      final error = await api.get('/x').then<ApiException?>((_) => null, onError: (Object e) => e as ApiException);

      expect(error!.code, 'RATE_LIMITED');
      expect(error.requestId, 'req-9');
      expect(error.retryAfter, const Duration(seconds: 42));
      expect(error.userMessage, contains('42 s'));
    });

    test('a non-envelope error keeps its status rather than failing to parse', () async {
      final api = client(
        MockClient((_) async => http.Response('<html>Bad gateway</html>', 502)),
        MemoryTokenStore(tokens('1')),
      );

      expect(api.get('/x'), throwsA(isA<ApiException>().having((e) => e.code, 'code', 'HTTP_502')));
    });

    test('an unreachable server is a network error', () async {
      final api = client(MockClient((_) async => throw http.ClientException('refused')), MemoryTokenStore(tokens('1')));

      expect(api.get('/x'), throwsA(isA<ApiException>().having((e) => e.isNetwork, 'network', isTrue)));
    });

    test('the access token is sent, and never on unauthenticated calls', () async {
      final seen = <String, String?>{};
      final api = client(
        MockClient((request) async {
          seen[request.url.path] = request.headers['authorization'];
          return json({'data': pairJson('2')});
        }),
        MemoryTokenStore(tokens('1')),
      );

      await api.get('/private');
      await api.post('/api/v1/auth/login', body: {}, authenticated: false);

      expect(seen['/private'], 'Bearer access-1');
      expect(seen['/api/v1/auth/login'], isNull);
    });
  });

  group('renewal', () {
    test('an expired access token is renewed before the request', () async {
      final paths = <String>[];
      final store = MemoryTokenStore(tokens('1', validFor: const Duration(seconds: 5)));
      final api = client(
        MockClient((request) async {
          paths.add(request.url.path);
          if (request.url.path == '/api/v1/auth/refresh') {
            expect(jsonDecode(request.body), {'refresh_token': 'refresh-1'});
            return json({'data': pairJson('2')});
          }
          expect(request.headers['authorization'], 'Bearer access-2');
          return json({'data': <String, Object>{}});
        }),
        store,
      );

      await api.get('/x');

      expect(paths, ['/api/v1/auth/refresh', '/x']);
      expect(store.tokens!.refreshToken, 'refresh-2');
    });

    test('a rejected request is renewed and retried once', () async {
      var attempts = 0;
      final api = client(
        MockClient((request) async {
          if (request.url.path == '/api/v1/auth/refresh') {
            return json({'data': pairJson('2')});
          }
          attempts++;
          return request.headers['authorization'] == 'Bearer access-2' ? json({'data': 'ok'}) : unauthenticated();
        }),
        MemoryTokenStore(tokens('1')),
      );

      expect((await api.get('/x')).data, 'ok');
      expect(attempts, 2);
    });

    test('concurrent requests share one renewal', () async {
      // Reusing a rotated refresh token revokes the whole session.
      var refreshes = 0;
      final release = Completer<void>();
      final api = client(
        MockClient((request) async {
          if (request.url.path == '/api/v1/auth/refresh') {
            refreshes++;
            await release.future;
            return json({'data': pairJson('2')});
          }
          return json({'data': 'ok'});
        }),
        MemoryTokenStore(tokens('1', validFor: Duration.zero)),
      );

      final requests = [for (var i = 0; i < 5; i++) api.get('/x/$i')];
      await Future<void>.delayed(Duration.zero);
      release.complete();
      await Future.wait(requests);

      expect(refreshes, 1);
    });

    test('a refused renewal ends the session', () async {
      var ended = false;
      final store = MemoryTokenStore(tokens('1', validFor: Duration.zero));
      final api = client(
        MockClient(
          (_) async => json({
            'error': {'code': 'INVALID_REFRESH_TOKEN', 'message': 'no'},
          }, status: 401),
        ),
        store,
      )..onSessionEnded = () => ended = true;

      await expectLater(api.get('/x'), throwsA(isA<ApiException>()));

      expect(ended, isTrue);
      expect(store.tokens, isNull);
    });

    test('being offline during renewal does not sign anyone out', () async {
      var ended = false;
      final store = MemoryTokenStore(tokens('1', validFor: Duration.zero));
      final api = client(MockClient((_) async => throw http.ClientException('offline')), store)
        ..onSessionEnded = () => ended = true;

      await expectLater(api.get('/x'), throwsA(isA<ApiException>().having((e) => e.isNetwork, 'network', isTrue)));

      expect(ended, isFalse);
      expect(store.tokens, isNotNull);
    });
  });

  group('signing out', () {
    test('local tokens are forgotten even when the server cannot be reached', () async {
      final store = MemoryTokenStore(tokens('1'));
      final api = client(MockClient((_) async => throw http.ClientException('offline')), store);

      await api.signOut();

      expect(store.tokens, isNull);
    });

    test('the refresh token is revoked on the server', () async {
      Object? sent;
      final api = client(
        MockClient((request) async {
          sent = jsonDecode(request.body);
          return http.Response('', 204);
        }),
        MemoryTokenStore(tokens('1')),
      );

      await api.signOut();

      expect(sent, {'refresh_token': 'refresh-1'});
    });
  });
}
