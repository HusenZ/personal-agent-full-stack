import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/config.dart';
import '../../app/server_address.dart';
import '../../core/api/api_client.dart';
import '../../core/auth/token_store.dart';
import '../../core/providers.dart';
import '../../core/ui/feedback.dart';

/// Debug builds only: points the app at a development backend.
Future<void> showServerSheet(BuildContext context) =>
    showModalBottomSheet<void>(context: context, isScrollControlled: true, builder: (_) => const _ServerSheet());

class _ServerSheet extends ConsumerStatefulWidget {
  const _ServerSheet();

  @override
  ConsumerState<_ServerSheet> createState() => _ServerSheetState();
}

enum _Check { idle, checking, reachable, unreachable }

class _ServerSheetState extends ConsumerState<_ServerSheet> {
  late final Uri _current = ref.read(serverAddressProvider);
  late ServerPreset? _preset = ServerPreset.values.where((preset) => preset.uri == _current).firstOrNull;
  late final _custom = TextEditingController(text: _preset == null ? _current.authority : '');
  _Check _check = _Check.idle;
  String? _checkMessage;
  bool _saving = false;

  @override
  void dispose() {
    _custom.dispose();
    super.dispose();
  }

  Uri? get _candidate => _preset?.uri ?? parseServerAddress(_custom.text);

  /// Asks the address for the backend's liveness probe, through the same
  /// client and envelope as every other request.
  Future<void> _test() async {
    final address = _candidate;
    if (address == null) {
      return;
    }
    setState(() {
      _check = _Check.checking;
      _checkMessage = null;
    });
    final probe = ApiClient(
      baseUrl: address,
      httpClient: ref.read(httpClientProvider),
      tokens: MemoryTokenStore(),
      requestTimeout: const Duration(seconds: 5),
    );
    try {
      await probe.send('GET', '/health', authenticated: false);
      if (mounted) {
        setState(() => _check = _Check.reachable);
      }
    } on Object catch (error) {
      if (mounted) {
        setState(() {
          _check = _Check.unreachable;
          _checkMessage = messageFor(error);
        });
      }
    }
  }

  Future<void> _use() async {
    final address = _candidate;
    if (address == null) {
      return;
    }
    setState(() => _saving = true);
    await ref.read(serverAddressProvider.notifier).change(address);
    if (mounted) {
      Navigator.of(context).pop();
    }
  }

  void _select(ServerPreset? preset) => setState(() {
    _preset = preset;
    _check = _Check.idle;
  });

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final customError = _preset == null && _custom.text.trim().isNotEmpty && parseServerAddress(_custom.text) == null
        ? 'Enter an address like 192.168.1.20:8000'
        : null;

    Widget option({
      required ServerPreset? preset,
      required IconData icon,
      required String title,
      required String subtitle,
    }) {
      final selected = _preset == preset;
      return Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Material(
          color: selected ? colors.primaryContainer : colors.surfaceContainerHigh,
          borderRadius: BorderRadius.circular(16),
          child: ListTile(
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
            selected: selected,
            leading: Icon(icon),
            title: Text(title),
            subtitle: Text(subtitle),
            trailing: selected ? Icon(Icons.check_circle, color: colors.primary) : null,
            onTap: () => _select(preset),
          ),
        ),
      );
    }

    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
      child: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text('Development server', style: theme.textTheme.titleLarge),
              const SizedBox(height: 4),
              Text(
                'Debug builds only. Changing the server signs you out.',
                style: theme.textTheme.bodyMedium?.copyWith(color: colors.onSurfaceVariant),
              ),
              const SizedBox(height: 20),
              for (final preset in ServerPreset.values)
                option(
                  preset: preset,
                  icon: switch (preset) {
                    ServerPreset.androidEmulator => Icons.android,
                    ServerPreset.genymotion => Icons.phone_android,
                    ServerPreset.thisMachine => Icons.laptop_mac,
                  },
                  title: '${preset.label}  ·  ${preset.uri.authority}',
                  subtitle: preset.description,
                ),
              option(
                preset: null,
                icon: Icons.wifi,
                title: 'Phone on the same Wi-Fi',
                subtitle: "Your computer's local IP address.",
              ),
              if (_preset == null) ...[
                const SizedBox(height: 8),
                TextField(
                  key: const Key('server-address'),
                  controller: _custom,
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                  onChanged: (_) => setState(() => _check = _Check.idle),
                  decoration: InputDecoration(
                    labelText: 'Server address',
                    hintText: '192.168.1.20:8000',
                    errorText: customError,
                    helperText: 'Start the backend with --host 0.0.0.0 so the phone can reach it.',
                    helperMaxLines: 2,
                  ),
                ),
              ],
              const SizedBox(height: 16),
              if (_check != _Check.idle) _CheckResult(check: _check, message: _checkMessage),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      key: const Key('test-server'),
                      onPressed: _candidate == null || _check == _Check.checking ? null : _test,
                      icon: const Icon(Icons.network_check),
                      label: const Text('Test'),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    flex: 2,
                    child: FilledButton(
                      key: const Key('use-server'),
                      style: FilledButton.styleFrom(minimumSize: const Size(0, 48)),
                      onPressed: _candidate == null || _saving ? null : _use,
                      child: const Text('Use this server'),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _CheckResult extends StatelessWidget {
  const _CheckResult({required this.check, required this.message});

  final _Check check;
  final String? message;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final (icon, color, text) = switch (check) {
      _Check.checking => (Icons.hourglass_top, colors.onSurfaceVariant, 'Checking…'),
      _Check.reachable => (Icons.check_circle, Colors.green.shade600, 'The backend answered.'),
      _ => (Icons.error_outline, colors.error, message ?? 'No answer from that address.'),
    };
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        children: [
          Icon(icon, size: 18, color: color),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              text,
              key: const Key('server-check'),
              style: TextStyle(color: color),
            ),
          ),
        ],
      ),
    );
  }
}
