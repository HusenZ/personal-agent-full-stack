import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/ui/feedback.dart';

/// Asks for the password before deleting the account, and says exactly what
/// goes with it. The server checks the password; this only collects it.
Future<void> showDeleteAccountDialog(BuildContext context) =>
    showDialog<void>(context: context, builder: (_) => const _DeleteAccountDialog());

class _DeleteAccountDialog extends ConsumerStatefulWidget {
  const _DeleteAccountDialog();

  @override
  ConsumerState<_DeleteAccountDialog> createState() => _DeleteAccountDialogState();
}

class _DeleteAccountDialogState extends ConsumerState<_DeleteAccountDialog> {
  final _password = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _password.dispose();
    super.dispose();
  }

  Future<void> _delete() async {
    if (_password.text.isEmpty) {
      setState(() => _error = 'Enter your password');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref.read(sessionProvider.notifier).deleteAccount(_password.text);
      if (mounted) {
        Navigator.of(context).pop();
      }
    } on Object catch (error) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = messageFor(error);
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    return AlertDialog(
      icon: Icon(Icons.warning_amber_rounded, color: colors.error),
      title: const Text('Delete your account?'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'This permanently deletes your profile, every memory, every conversation and your analysis. '
              'It cannot be undone.',
            ),
            const SizedBox(height: 16),
            TextField(
              key: const Key('delete-account-password'),
              controller: _password,
              obscureText: true,
              enabled: !_busy,
              autofillHints: const [AutofillHints.password],
              onSubmitted: (_) => _busy ? null : _delete(),
              decoration: InputDecoration(labelText: 'Password', errorText: _error),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(onPressed: _busy ? null : () => Navigator.of(context).pop(), child: const Text('Cancel')),
        FilledButton(
          key: const Key('delete-account-confirm'),
          style: FilledButton.styleFrom(
            minimumSize: const Size(88, 44),
            backgroundColor: colors.error,
            foregroundColor: colors.onError,
          ),
          onPressed: _busy ? null : _delete,
          child: Text(_busy ? 'Deleting…' : 'Delete everything'),
        ),
      ],
    );
  }
}
