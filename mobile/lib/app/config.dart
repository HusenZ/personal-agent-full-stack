import 'package:flutter/foundation.dart';

/// How to reach the backend.
///
/// A release build talks only to the HTTPS address it was built with. A debug
/// build defaults to the development machine as seen from where the app runs,
/// and lets the developer pick another address in the app, because that
/// address differs between an emulator, a simulator and a phone on Wi-Fi.
class AppConfig {
  const AppConfig({required this.apiBaseUrl, this.canChangeServer = false});

  final Uri apiBaseUrl;

  /// Whether the server address may be changed from inside the app.
  final bool canChangeServer;

  static const defaultPort = 8000;

  static AppConfig fromEnvironment() {
    const configured = String.fromEnvironment('API_BASE_URL');
    if (kReleaseMode) {
      if (configured.isEmpty) {
        throw StateError('API_BASE_URL must be set for a release build.');
      }
      final uri = Uri.parse(configured);
      if (uri.scheme != 'https') {
        throw StateError('A release build must reach the API over HTTPS.');
      }
      return AppConfig(apiBaseUrl: uri);
    }
    if (configured.isNotEmpty) {
      return AppConfig(apiBaseUrl: Uri.parse(configured), canChangeServer: true);
    }
    final host = defaultTargetPlatform == TargetPlatform.android
        ? ServerPreset.androidEmulator.host
        : ServerPreset.thisMachine.host;
    return AppConfig(
      apiBaseUrl: Uri(scheme: 'http', host: host, port: defaultPort),
      canChangeServer: true,
    );
  }
}

/// The usual places a development backend is reachable from.
enum ServerPreset {
  /// The Android emulator's alias for the machine running it.
  androidEmulator('10.0.2.2', 'Android emulator', 'Reaches localhost on the computer running the emulator.'),

  /// Genymotion's equivalent of the alias above.
  genymotion('10.0.3.2', 'Genymotion', 'Reaches localhost on the computer running Genymotion.'),

  /// The iOS simulator shares the Mac's network, and a USB-connected Android
  /// device reaches the computer this way after `adb reverse tcp:8000 tcp:8000`.
  thisMachine('localhost', 'iOS simulator or adb reverse', 'The simulator, or a device after adb reverse.');

  const ServerPreset(this.host, this.label, this.description);

  final String host;
  final String label;
  final String description;

  Uri get uri => Uri(scheme: 'http', host: host, port: AppConfig.defaultPort);
}

/// Reads what a developer typed: `192.168.1.20`, `192.168.1.20:8000` or a
/// full `http(s)://` address. A bare host means the development server's
/// port. Returns null if it is not a usable server root.
Uri? parseServerAddress(String input) {
  var text = input.trim();
  if (text.isEmpty) {
    return null;
  }
  final bare = !text.contains('://');
  if (bare) {
    text = 'http://$text';
  }
  final uri = Uri.tryParse(text);
  if (uri == null || !{'http', 'https'}.contains(uri.scheme) || uri.host.isEmpty) {
    return null;
  }
  if (uri.hasQuery || uri.hasFragment || uri.userInfo.isNotEmpty || (uri.path.isNotEmpty && uri.path != '/')) {
    return null;
  }
  final port = uri.hasPort ? uri.port : (bare ? AppConfig.defaultPort : null);
  return Uri(scheme: uri.scheme, host: uri.host, port: port);
}
