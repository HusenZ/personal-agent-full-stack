import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers.dart';

enum SessionStatus { restoring, signedOut, signedIn }

/// Whether someone is signed in, which is what decides where the app can go.
class SessionController extends Notifier<SessionStatus> {
  @override
  SessionStatus build() {
    _restore();
    return SessionStatus.restoring;
  }

  Future<void> _restore() async {
    final hasSession = await ref.read(apiClientProvider).hasSession();
    if (ref.mounted && state == SessionStatus.restoring) {
      state = hasSession ? SessionStatus.signedIn : SessionStatus.signedOut;
    }
  }

  Future<void> signIn(String email, String password) async {
    await ref.read(apiClientProvider).signIn(email.trim(), password);
    state = SessionStatus.signedIn;
  }

  Future<void> register(String email, String password) async {
    await ref
        .read(apiClientProvider)
        .post('/api/v1/auth/register', body: {'email': email.trim(), 'password': password}, authenticated: false);
    await signIn(email, password);
  }

  Future<void> signOut() async {
    await ref.read(apiClientProvider).signOut();
    state = SessionStatus.signedOut;
  }

  /// Deletes the account and everything stored about the user, then forgets
  /// the session locally; the server has already revoked it.
  Future<void> deleteAccount(String password) async {
    await ref.read(apiClientProvider).send('DELETE', '/api/v1/auth/account', body: {'password': password});
    await ref.read(tokenStoreProvider).clear();
    state = SessionStatus.signedOut;
  }

  /// The server would not renew the session: revoked, expired, or reused.
  void sessionEnded() {
    if (state != SessionStatus.signedOut) {
      state = SessionStatus.signedOut;
    }
  }
}
