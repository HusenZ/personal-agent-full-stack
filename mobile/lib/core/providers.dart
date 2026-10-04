import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../app/config.dart';
import '../app/server_address.dart';
import 'api/api_client.dart';
import 'auth/session_controller.dart';
import 'auth/token_store.dart';

/// Overridden in main() with the configuration the app was built with.
final appConfigProvider = Provider<AppConfig>(
  (ref) => throw UnimplementedError('appConfigProvider must be overridden'),
);

final tokenStoreProvider = Provider<TokenStore>((ref) => SecureTokenStore());

final httpClientProvider = Provider<http.Client>((ref) {
  final client = http.Client();
  ref.onDispose(client.close);
  return client;
});

final apiClientProvider = Provider<ApiClient>((ref) {
  final client = ApiClient(
    baseUrl: ref.watch(serverAddressProvider),
    httpClient: ref.watch(httpClientProvider),
    tokens: ref.watch(tokenStoreProvider),
  );
  client.onSessionEnded = () => ref.read(sessionProvider.notifier).sessionEnded();
  return client;
});

final sessionProvider = NotifierProvider<SessionController, SessionStatus>(SessionController.new);
