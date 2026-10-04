import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/providers.dart';
import '../../app/server_address.dart';
import '../../core/ui/components.dart';
import '../../core/ui/feedback.dart';
import 'server_sheet.dart';

/// The server's rule, repeated so it can be explained before a round trip.
/// The server still enforces it; this only makes the form helpful.
const minimumPasswordLength = 12;

String? validateEmail(String? value) {
  final email = value?.trim() ?? '';
  if (email.isEmpty) {
    return 'Enter your email';
  }
  if (!RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$').hasMatch(email)) {
    return 'Enter a valid email';
  }
  return null;
}

class SignInScreen extends ConsumerStatefulWidget {
  const SignInScreen({super.key, this.registering = false});

  /// Opens on the new-account form, e.g. from the welcome screen.
  final bool registering;

  @override
  ConsumerState<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends ConsumerState<SignInScreen> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  late bool _registering = widget.registering;
  bool _busy = false;
  bool _obscured = true;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_form.currentState!.validate()) {
      return;
    }
    setState(() => _busy = true);
    final session = ref.read(sessionProvider.notifier);
    try {
      if (_registering) {
        await session.register(_email.text, _password.text);
      } else {
        await session.signIn(_email.text, _password.text);
      }
    } on Object catch (error) {
      if (mounted) {
        showError(context, error);
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  void _setMode(bool registering) {
    if (registering != _registering) {
      setState(() => _registering = registering);
      _form.currentState?.reset();
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final canChangeServer = ref.watch(appConfigProvider).canChangeServer;

    return Scaffold(
      extendBodyBehindAppBar: true,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        leading: IconButton(
          tooltip: 'Back',
          icon: const Icon(Icons.arrow_back),
          onPressed: () => context.go('/welcome'),
        ),
      ),
      body: DecoratedBox(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [colors.primaryContainer.withValues(alpha: 0.55), colors.surface],
            stops: const [0, 0.45],
          ),
        ),
        child: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 420),
                child: Form(
                  key: _form,
                  child: AutofillGroup(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        const Center(child: BrandMark()),
                        const SizedBox(height: 24),
                        AnimatedSwitcher(
                          duration: const Duration(milliseconds: 200),
                          child: Text(
                            _registering ? 'Create your account' : 'Welcome back',
                            key: ValueKey(_registering),
                            style: theme.textTheme.headlineMedium,
                            textAlign: TextAlign.center,
                          ),
                        ),
                        const SizedBox(height: 8),
                        Text(
                          'An assistant that learns what matters to you.',
                          style: theme.textTheme.bodyLarge?.copyWith(color: colors.onSurfaceVariant),
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: 32),
                        SegmentedButton<bool>(
                          showSelectedIcon: false,
                          segments: const [
                            ButtonSegment(value: false, label: Text('Sign in', key: Key('mode-sign-in'))),
                            ButtonSegment(value: true, label: Text('New account', key: Key('mode-register'))),
                          ],
                          selected: {_registering},
                          onSelectionChanged: _busy ? null : (selection) => _setMode(selection.first),
                        ),
                        const SizedBox(height: 24),
                        TextFormField(
                          key: const Key('email'),
                          controller: _email,
                          decoration: const InputDecoration(
                            labelText: 'Email',
                            prefixIcon: Icon(Icons.alternate_email),
                          ),
                          keyboardType: TextInputType.emailAddress,
                          autofillHints: const [AutofillHints.email],
                          textInputAction: TextInputAction.next,
                          autocorrect: false,
                          enabled: !_busy,
                          validator: validateEmail,
                        ),
                        const SizedBox(height: 14),
                        TextFormField(
                          key: const Key('password'),
                          controller: _password,
                          obscureText: _obscured,
                          enabled: !_busy,
                          decoration: InputDecoration(
                            labelText: 'Password',
                            prefixIcon: const Icon(Icons.lock_outline),
                            helperText: _registering ? 'At least $minimumPasswordLength characters' : null,
                            suffixIcon: IconButton(
                              tooltip: _obscured ? 'Show password' : 'Hide password',
                              icon: Icon(_obscured ? Icons.visibility_outlined : Icons.visibility_off_outlined),
                              onPressed: () => setState(() => _obscured = !_obscured),
                            ),
                          ),
                          autofillHints: [_registering ? AutofillHints.newPassword : AutofillHints.password],
                          textInputAction: TextInputAction.done,
                          onFieldSubmitted: (_) => _busy ? null : _submit(),
                          validator: (value) {
                            final password = value ?? '';
                            if (password.isEmpty) {
                              return 'Enter your password';
                            }
                            if (_registering && password.length < minimumPasswordLength) {
                              return 'Use at least $minimumPasswordLength characters';
                            }
                            return null;
                          },
                        ),
                        const SizedBox(height: 28),
                        FilledButton(
                          key: const Key('submit'),
                          onPressed: _busy ? null : _submit,
                          child: _busy ? const ButtonProgress() : Text(_registering ? 'Create account' : 'Sign in'),
                        ),
                        const SizedBox(height: 20),
                        Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Icon(Icons.shield_outlined, size: 16, color: colors.onSurfaceVariant),
                            const SizedBox(width: 6),
                            Flexible(
                              child: Text(
                                'You can review, correct or delete anything it remembers.',
                                style: theme.textTheme.bodySmall?.copyWith(color: colors.onSurfaceVariant),
                                textAlign: TextAlign.center,
                              ),
                            ),
                          ],
                        ),
                        if (canChangeServer) ...[const SizedBox(height: 28), const _ServerChip()],
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Shows which backend a debug build is talking to, and lets it be changed.
class _ServerChip extends ConsumerWidget {
  const _ServerChip();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final address = ref.watch(serverAddressProvider);
    return Center(
      child: ActionChip(
        key: const Key('server'),
        avatar: const Icon(Icons.dns_outlined, size: 18),
        label: Text('Server: ${address.authority}'),
        tooltip: 'Change development server',
        onPressed: () => showServerSheet(context),
      ),
    );
  }
}
