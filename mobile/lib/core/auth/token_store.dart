import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// The access and refresh tokens, and when the access token expires.
class TokenPair {
  const TokenPair({required this.accessToken, required this.refreshToken, required this.accessExpiresAt});

  factory TokenPair.fromJson(Map<String, dynamic> json) => TokenPair(
    accessToken: json['access_token'] as String,
    refreshToken: json['refresh_token'] as String,
    accessExpiresAt: DateTime.parse(json['access_token_expires_at'] as String),
  );

  final String accessToken;
  final String refreshToken;
  final DateTime accessExpiresAt;

  /// Treated as expired slightly early, so a request does not set off with a
  /// token that lapses before the server reads it.
  bool expiresWithin(Duration margin, DateTime now) => !accessExpiresAt.isAfter(now.add(margin));

  Map<String, dynamic> toJson() => {
    'access_token': accessToken,
    'refresh_token': refreshToken,
    'access_token_expires_at': accessExpiresAt.toUtc().toIso8601String(),
  };
}

abstract interface class TokenStore {
  Future<TokenPair?> read();
  Future<void> write(TokenPair tokens);
  Future<void> clear();
}

/// Tokens in the platform's secure storage: the Keychain on iOS, a Keystore
/// wrapped key on Android. Never in shared preferences or a plain file.
class SecureTokenStore implements TokenStore {
  SecureTokenStore([FlutterSecureStorage? storage])
    : _storage =
          storage ??
          const FlutterSecureStorage(
            // This device only, so backups can't restore a session elsewhere.
            iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock_this_device),
          );

  static const _key = 'session_tokens';
  final FlutterSecureStorage _storage;

  @override
  Future<TokenPair?> read() async {
    final raw = await _storage.read(key: _key);
    if (raw == null) {
      return null;
    }
    try {
      return TokenPair.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    } on Object {
      // Unreadable data is treated as signed out.
      await clear();
      return null;
    }
  }

  @override
  Future<void> write(TokenPair tokens) => _storage.write(key: _key, value: jsonEncode(tokens.toJson()));

  @override
  Future<void> clear() => _storage.delete(key: _key);
}

/// Holds tokens in memory, for tests.
class MemoryTokenStore implements TokenStore {
  MemoryTokenStore([this.tokens]);

  TokenPair? tokens;

  @override
  Future<TokenPair?> read() async => tokens;

  @override
  Future<void> write(TokenPair value) async => tokens = value;

  @override
  Future<void> clear() async => tokens = null;
}
