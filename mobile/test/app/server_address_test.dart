import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:personal_intelligence/app/config.dart';
import 'package:personal_intelligence/app/server_address.dart';
import 'package:personal_intelligence/core/auth/session_controller.dart';
import 'package:personal_intelligence/core/auth/token_store.dart';
import 'package:personal_intelligence/core/providers.dart';
import 'package:personal_intelligence/features/auth/sign_in_screen.dart';

void main() {
  group('reading a typed server address', () {
    test('a bare LAN address means the development port over HTTP', () {
      expect(parseServerAddress('192.168.1.20'), Uri.parse('http://192.168.1.20:8000'));
      expect(parseServerAddress(' 192.168.1.20:9000 '), Uri.parse('http://192.168.1.20:9000'));
    });

    test('a full address keeps its scheme and port', () {
      expect(parseServerAddress('https://api.example.com'), Uri.parse('https://api.example.com'));
      expect(parseServerAddress('http://10.0.2.2:8000/'), Uri.parse('http://10.0.2.2:8000'));
    });

    test('anything that is not a server root is refused', () {
      for (final input in ['', 'ftp://host', 'http://', 'http://host/api/v1', 'host?x=1', 'user:pw@host']) {
        expect(parseServerAddress(input), isNull, reason: input);
      }
    });

    test('the presets are the documented development addresses', () {
      expect(ServerPreset.androidEmulator.uri, Uri.parse('http://10.0.2.2:8000'));
      expect(ServerPreset.genymotion.uri, Uri.parse('http://10.0.3.2:8000'));
      expect(ServerPreset.thisMachine.uri, Uri.parse('http://localhost:8000'));
    });
  });

  group('changing the server', () {
    late MemoryTokenStore tokens;
    late MemoryServerAddressStore saved;
    late List<Uri> requests;

    ProviderContainer container({required bool canChange}) {
      tokens = MemoryTokenStore();
      saved = MemoryServerAddressStore();
      requests = [];
      final client = MockClient((request) async {
        requests.add(request.url);
        return http.Response(jsonEncode({'data': null}), 200);
      });
      final container = ProviderContainer(
        overrides: [
          appConfigProvider.overrideWithValue(
            AppConfig(apiBaseUrl: Uri.parse('http://10.0.2.2:8000'), canChangeServer: canChange),
          ),
          tokenStoreProvider.overrideWithValue(tokens),
          serverAddressStoreProvider.overrideWithValue(saved),
          httpClientProvider.overrideWithValue(client),
        ],
        retry: (_, _) => null,
      );
      addTearDown(container.dispose);
      return container;
    }

    test('ends the session with the old server, then remembers the new one', () async {
      final app = container(canChange: true);
      await tokens.write(
        TokenPair(accessToken: 'a', refreshToken: 'r', accessExpiresAt: DateTime.now().add(const Duration(minutes: 5))),
      );
      final address = Uri.parse('http://192.168.1.20:8000');

      await app.read(serverAddressProvider.notifier).change(address);

      expect(tokens.tokens, isNull);
      expect(requests.single.host, '10.0.2.2', reason: 'the logout goes to the server that issued the token');
      expect(saved.address, address);
      expect(app.read(apiClientProvider).baseUrl, address);
      expect(app.read(sessionProvider), SessionStatus.signedOut);
    });

    test('a build that may not change server ignores the request', () async {
      final app = container(canChange: false);

      await app.read(serverAddressProvider.notifier).change(Uri.parse('http://evil.test'));

      expect(app.read(serverAddressProvider), Uri.parse('http://10.0.2.2:8000'));
      expect(saved.address, isNull);
    });
  });

  group('the sign-in screen', () {
    Future<void> pump(WidgetTester tester, {required bool canChange}) async {
      // A phone-sized screen, so the whole server sheet fits.
      tester.view.physicalSize = const Size(1080, 2400);
      tester.view.devicePixelRatio = 2.75;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            appConfigProvider.overrideWithValue(
              AppConfig(apiBaseUrl: Uri.parse('http://10.0.2.2:8000'), canChangeServer: canChange),
            ),
            tokenStoreProvider.overrideWithValue(MemoryTokenStore()),
            serverAddressStoreProvider.overrideWithValue(MemoryServerAddressStore()),
            httpClientProvider.overrideWithValue(MockClient((_) async => http.Response('{}', 200))),
          ],
          retry: (_, _) => null,
          child: const MaterialApp(home: SignInScreen()),
        ),
      );
      await tester.pump();
    }

    testWidgets('a debug build shows the server and lets it be changed', (tester) async {
      await pump(tester, canChange: true);

      expect(find.text('Server: 10.0.2.2:8000'), findsOneWidget);
      await tester.ensureVisible(find.byKey(const Key('server')));
      await tester.tap(find.byKey(const Key('server')));
      await tester.pumpAndSettle();

      await tester.tap(find.text('Phone on the same Wi-Fi'));
      await tester.pump();
      await tester.enterText(find.byKey(const Key('server-address')), '192.168.1.20');
      await tester.pump();
      await tester.tap(find.byKey(const Key('use-server')));
      await tester.pumpAndSettle();

      expect(find.text('Server: 192.168.1.20:8000'), findsOneWidget);
    });

    testWidgets('a release build never offers to change the server', (tester) async {
      await pump(tester, canChange: false);

      expect(find.byKey(const Key('server')), findsNothing);
    });
  });
}
