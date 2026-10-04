import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app/app.dart';
import 'app/config.dart';
import 'app/server_address.dart';
import 'core/providers.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  var config = AppConfig.fromEnvironment();
  if (config.canChangeServer) {
    try {
      final chosen = await SecureServerAddressStore().read();
      if (chosen != null) {
        config = AppConfig(apiBaseUrl: chosen, canChangeServer: true);
      }
    } on Object catch (error) {
      // Debug only: falls back to the default address.
      debugPrint('Could not read the saved server address: ${error.runtimeType}');
    }
  }
  runApp(
    ProviderScope(
      overrides: [appConfigProvider.overrideWithValue(config)],
      // No automatic retries: they'd repeat paid, rate-limited calls.
      retry: (_, _) => null,
      child: const PersonalIntelligenceApp(),
    ),
  );
}
