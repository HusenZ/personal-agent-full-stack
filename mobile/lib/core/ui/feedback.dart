import 'package:flutter/material.dart';

import '../api/api_exception.dart';
import 'components.dart';

String messageFor(Object error) =>
    error is ApiException ? error.userMessage : 'Something went wrong. Please try again.';

void showError(BuildContext context, Object error) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(messageFor(error))));
}

/// A full-area error with a retry, for screens whose content failed to load.
class ErrorView extends StatelessWidget {
  const ErrorView({super.key, required this.error, required this.onRetry});

  final Object error;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => EmptyState(
    icon: Icons.cloud_off_outlined,
    tone: Theme.of(context).colorScheme.error,
    title: "Couldn't load this",
    message: messageFor(error),
    action: FilledButton.tonalIcon(
      onPressed: onRetry,
      icon: const Icon(Icons.refresh),
      label: const Text('Try again'),
      style: FilledButton.styleFrom(minimumSize: const Size(160, 48)),
    ),
  );
}

/// A brief confirmation, optionally with a way to take it back.
void showMessage(BuildContext context, String message, {String? actionLabel, VoidCallback? onAction}) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(
      SnackBar(
        content: Text(message),
        action: actionLabel == null || onAction == null
            ? null
            : SnackBarAction(label: actionLabel, onPressed: onAction),
      ),
    );
}

/// Asks before something that cannot be undone.
Future<bool> confirm(
  BuildContext context, {
  required String title,
  required String message,
  required String action,
  bool destructive = false,
}) async {
  final confirmed = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(title),
      content: Text(message),
      actions: [
        TextButton(onPressed: () => Navigator.of(context).pop(false), child: const Text('Cancel')),
        FilledButton(
          style: FilledButton.styleFrom(
            minimumSize: const Size(88, 44),
            backgroundColor: destructive ? Theme.of(context).colorScheme.error : null,
            foregroundColor: destructive ? Theme.of(context).colorScheme.onError : null,
          ),
          onPressed: () => Navigator.of(context).pop(true),
          child: Text(action),
        ),
      ],
    ),
  );
  return confirmed ?? false;
}
