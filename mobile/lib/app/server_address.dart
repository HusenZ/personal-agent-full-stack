import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../core/providers.dart';

/// Remembers the server a debug build was pointed at, across restarts.
abstract interface class ServerAddressStore {
  Future<Uri?> read();

  /// Null forgets the choice, returning to the build's default.
  Future<void> write(Uri? address);
}

/// Kept with the app's other stored values rather than adding a preferences
/// package for one string.
class SecureServerAddressStore implements ServerAddressStore {
  SecureServerAddressStore([FlutterSecureStorage? storage]) : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;

  static const _key = 'debug_api_base_url';

  @override
  Future<Uri?> read() async {
    final value = await _storage.read(key: _key);
    return value == null ? null : Uri.tryParse(value);
  }

  @override
  Future<void> write(Uri? address) =>
      address == null ? _storage.delete(key: _key) : _storage.write(key: _key, value: address.toString());
}

class MemoryServerAddressStore implements ServerAddressStore {
  MemoryServerAddressStore([this.address]);

  Uri? address;

  @override
  Future<Uri?> read() async => address;

  @override
  Future<void> write(Uri? address) async => this.address = address;
}

final serverAddressStoreProvider = Provider<ServerAddressStore>((ref) => SecureServerAddressStore());

/// The server the app talks to now. Starts from the build's configuration;
/// a debug build may switch it while signed out.
class ServerAddressController extends Notifier<Uri> {
  @override
  Uri build() => ref.watch(appConfigProvider).apiBaseUrl;

  bool get canChange => ref.read(appConfigProvider).canChangeServer;

  /// Tokens are issued by one server and mean nothing to another, so the
  /// session is ended before the address changes.
  Future<void> change(Uri address) async {
    if (!canChange || address == state) {
      return;
    }
    await ref.read(sessionProvider.notifier).signOut();
    await ref.read(serverAddressStoreProvider).write(address);
    state = address;
  }
}

final serverAddressProvider = NotifierProvider<ServerAddressController, Uri>(ServerAddressController.new);
