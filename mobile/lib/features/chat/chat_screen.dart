import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/ui/components.dart';
import '../../core/ui/feedback.dart';
import '../../core/ui/formatting.dart';
import 'answer_receipt_view.dart';
import 'chat_controller.dart';
import 'chat_repository.dart';
import 'conversations_controller.dart';
import 'conversations_screen.dart' show confirmDeleteConversation, starterPrompts;

class ChatScreen extends ConsumerStatefulWidget {
  /// Null opens a new conversation.
  const ChatScreen({super.key, this.conversationId, this.draft});

  final String? conversationId;

  /// Text to start the composer with, e.g. a suggestion the user picked.
  final String? draft;

  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  late final String _key = widget.conversationId == null
      ? 'new:${identityHashCode(this)}:${DateTime.now().microsecondsSinceEpoch}'
      : 'conversation:${widget.conversationId}';
  late final _input = TextEditingController(text: widget.draft);
  final _focus = FocusNode();

  @override
  void dispose() {
    _input.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _send() {
    final text = _input.text;
    if (text.trim().isEmpty) {
      return;
    }
    ref.read(chatControllerProvider(_key).notifier).send(text);
    _input.clear();
  }

  Future<void> _delete(String id) async {
    if (!await confirmDeleteConversation(context)) {
      return;
    }
    try {
      await ref.read(chatRepositoryProvider).deleteConversation(id);
      ref.invalidate(conversationsProvider);
      if (mounted) {
        Navigator.of(context).pop();
      }
    } on Object catch (error) {
      if (mounted) {
        showError(context, error);
      }
    }
  }

  void _useSuggestion(String text) {
    _input
      ..text = text
      ..selection = TextSelection.collapsed(offset: text.length);
    _focus.requestFocus();
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(chatControllerProvider(_key));
    final controller = ref.read(chatControllerProvider(_key).notifier);

    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            const AssistantAvatar(size: 34),
            const SizedBox(width: 12),
            Expanded(child: Text(state.title ?? 'New chat', maxLines: 1, overflow: TextOverflow.ellipsis)),
          ],
        ),
        actions: [
          if (state.conversationId case final id? when !state.sending)
            PopupMenuButton<String>(
              tooltip: 'Conversation options',
              onSelected: (_) => _delete(id),
              itemBuilder: (context) => [
                PopupMenuItem(
                  value: 'delete',
                  child: ListTile(
                    leading: Icon(Icons.delete_outline, color: Theme.of(context).colorScheme.error),
                    title: const Text('Delete conversation'),
                  ),
                ),
              ],
            ),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: switch (state) {
                ChatState(loading: true) => const Center(child: CircularProgressIndicator()),
                ChatState(:final Object loadError) => ErrorView(error: loadError, onRetry: controller.load),
                ChatState(messages: []) => _Welcome(onSuggestion: _useSuggestion),
                _ => _Messages(state: state, onLoadEarlier: controller.loadEarlier),
              },
            ),
            if (state.lastFailure case final failure?)
              _FailureBar(message: messageFor(failure), onRetry: controller.retry),
            _Composer(controller: _input, focusNode: _focus, enabled: !state.sending && !state.loading, onSend: _send),
          ],
        ),
      ),
    );
  }
}

class _Messages extends StatelessWidget {
  const _Messages({required this.state, required this.onLoadEarlier});

  final ChatState state;
  final VoidCallback onLoadEarlier;

  @override
  Widget build(BuildContext context) {
    final messages = state.messages.reversed.toList();
    final thinking = state.stage == null ? 0 : 1;
    final extra = state.hasEarlier ? 1 : 0;
    final now = DateTime.now();
    // Reversed so new messages appear at the bottom.
    return ListView.builder(
      reverse: true,
      keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
      itemCount: thinking + messages.length + extra,
      itemBuilder: (context, index) {
        if (thinking == 1 && index == 0) {
          return _Thinking(stage: state.stage!);
        }
        final position = index - thinking;
        if (position == messages.length) {
          return Center(
            child: TextButton.icon(
              onPressed: state.loadingEarlier ? null : onLoadEarlier,
              icon: const Icon(Icons.history, size: 18),
              label: Text(state.loadingEarlier ? 'Loading…' : 'Load earlier messages'),
            ),
          );
        }
        final message = messages[position];
        // The list runs newest first, so the next item is the older one.
        final older = position + 1 < messages.length ? messages[position + 1] : null;
        final day = message.createdAt;
        final olderDay = older?.createdAt;
        // The oldest loaded message only opens a day if nothing earlier exists.
        final startsDay =
            day != null && (olderDay == null ? older == null && !state.hasEarlier : !isSameDay(olderDay, day));
        final sameSenderAsOlder = older != null && older.role == message.role && !startsDay;
        return Column(
          children: [
            if (startsDay) _DaySeparator(label: dayLabel(day, now: now)),
            Padding(
              padding: EdgeInsets.only(top: sameSenderAsOlder ? 2 : 10),
              child: MessageBubble(message: message, showAvatar: !sameSenderAsOlder),
            ),
          ],
        );
      },
    );
  }
}

class _DaySeparator extends StatelessWidget {
  const _DaySeparator({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Center(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
          decoration: BoxDecoration(
            color: theme.colorScheme.surfaceContainerHigh,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Text(label, style: theme.textTheme.labelSmall?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
        ),
      ),
    );
  }
}

class MessageBubble extends StatelessWidget {
  const MessageBubble({super.key, required this.message, this.showAvatar = true});

  final ChatMessage message;

  /// False for a message that follows another from the same sender.
  final bool showAvatar;

  void _copy(BuildContext context) {
    Clipboard.setData(ClipboardData(text: message.content));
    HapticFeedback.selectionClick();
    showMessage(context, 'Copied');
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final mine = message.role == MessageRole.user;

    final notice = switch (message) {
      ChatMessage(degraded: true) => "I couldn't reach the assistant, so this is a placeholder. Try again in a moment.",
      ChatMessage(grounded: false) => "Parts of this reply couldn't be checked against what you've told me.",
      _ => null,
    };

    final background = message.failed ? colors.errorContainer : (mine ? colors.primary : colors.surfaceContainerHigh);
    final foreground = message.failed ? colors.onErrorContainer : (mine ? colors.onPrimary : colors.onSurface);
    const radius = Radius.circular(20);
    const tail = Radius.circular(6);

    final status = message.failed
        ? 'Not sent'
        : message.pending
        ? 'Sending…'
        : (message.createdAt == null ? null : formatTimestamp(message.createdAt!));

    final bubble = GestureDetector(
      onLongPress: () => _copy(context),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 11),
        decoration: BoxDecoration(
          color: background,
          borderRadius: BorderRadius.only(
            topLeft: mine || !showAvatar ? radius : tail,
            topRight: !mine || !showAvatar ? radius : tail,
            bottomLeft: radius,
            bottomRight: radius,
          ),
        ),
        child: Text(message.content, style: theme.textTheme.bodyLarge?.copyWith(color: foreground, height: 1.4)),
      ),
    );

    return Row(
      mainAxisAlignment: mine ? MainAxisAlignment.end : MainAxisAlignment.start,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (!mine) ...[
          SizedBox(width: 30, child: showAvatar ? const AssistantAvatar() : null),
          const SizedBox(width: 8),
        ],
        Flexible(
          child: ConstrainedBox(
            constraints: BoxConstraints(maxWidth: MediaQuery.sizeOf(context).width * (mine ? 0.78 : 0.82)),
            child: Column(
              crossAxisAlignment: mine ? CrossAxisAlignment.end : CrossAxisAlignment.start,
              children: [
                bubble,
                if (!mine && message.receipt != null && !message.degraded)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: ReceiptLine(receipt: message.receipt!),
                  ),
                if (notice != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(Icons.info_outline, size: 15, color: colors.tertiary),
                        const SizedBox(width: 6),
                        Flexible(
                          child: Text(notice, style: theme.textTheme.bodySmall?.copyWith(color: colors.tertiary)),
                        ),
                      ],
                    ),
                  ),
                if (status != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 4, left: 4, right: 4),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (message.failed) ...[
                          Icon(Icons.error_outline, size: 13, color: colors.error),
                          const SizedBox(width: 4),
                        ],
                        Text(
                          status,
                          style: theme.textTheme.labelSmall?.copyWith(
                            color: message.failed ? colors.error : colors.onSurfaceVariant,
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

/// The assistant's turn in progress: what it is doing, where its reply will be.
class _Thinking extends StatelessWidget {
  const _Thinking({required this.stage});

  final TurnStage stage;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const AssistantAvatar(),
          const SizedBox(width: 8),
          Flexible(
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
              decoration: BoxDecoration(
                color: colors.surfaceContainerHigh,
                borderRadius: const BorderRadius.only(
                  topLeft: Radius.circular(6),
                  topRight: Radius.circular(20),
                  bottomLeft: Radius.circular(20),
                  bottomRight: Radius.circular(20),
                ),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const _Dots(),
                  const SizedBox(width: 10),
                  Flexible(
                    child: AnimatedSwitcher(
                      duration: const Duration(milliseconds: 250),
                      child: Text(
                        stage.label,
                        key: const Key('turn-stage'),
                        style: theme.textTheme.bodyMedium?.copyWith(color: colors.onSurfaceVariant),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _Dots extends StatefulWidget {
  const _Dots();

  @override
  State<_Dots> createState() => _DotsState();
}

class _DotsState extends State<_Dots> with SingleTickerProviderStateMixin {
  late final _controller = AnimationController(vsync: this, duration: const Duration(milliseconds: 1200))..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final color = Theme.of(context).colorScheme.primary;
    return ExcludeSemantics(
      child: AnimatedBuilder(
        animation: _controller,
        builder: (context, _) => Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            for (var dot = 0; dot < 3; dot++)
              Container(
                margin: const EdgeInsets.symmetric(horizontal: 2),
                width: 7,
                height: 7,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: color.withValues(alpha: _opacity(dot)),
                ),
              ),
          ],
        ),
      ),
    );
  }

  // Each dot brightens a third of a cycle after the one before it.
  double _opacity(int dot) {
    final phase = (_controller.value - dot / 3) % 1.0;
    return 0.25 + 0.75 * (phase < 0.5 ? 1 - phase * 2 : 0);
  }
}

class _FailureBar extends StatelessWidget {
  const _FailureBar({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      margin: const EdgeInsets.fromLTRB(12, 4, 12, 4),
      padding: const EdgeInsets.only(left: 14, right: 4),
      decoration: BoxDecoration(color: colors.errorContainer, borderRadius: BorderRadius.circular(14)),
      child: Row(
        children: [
          Icon(Icons.error_outline, size: 20, color: colors.onErrorContainer),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              message,
              key: const Key('turn-failure'),
              style: TextStyle(color: colors.onErrorContainer),
            ),
          ),
          TextButton(onPressed: onRetry, child: const Text('Retry')),
        ],
      ),
    );
  }
}

class _Composer extends StatelessWidget {
  const _Composer({required this.controller, required this.focusNode, required this.enabled, required this.onSend});

  final TextEditingController controller;
  final FocusNode focusNode;
  final bool enabled;
  final VoidCallback onSend;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 8, 8, 10),
      decoration: BoxDecoration(
        color: colors.surface,
        border: Border(top: BorderSide(color: colors.outlineVariant.withValues(alpha: 0.5))),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: TextField(
              key: const Key('message-input'),
              controller: controller,
              focusNode: focusNode,
              minLines: 1,
              maxLines: 6,
              maxLength: 4000,
              textCapitalization: TextCapitalization.sentences,
              decoration: InputDecoration(
                hintText: 'Message',
                counterText: '',
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(24), borderSide: BorderSide.none),
                enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(24), borderSide: BorderSide.none),
                focusedBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(24),
                  borderSide: BorderSide(color: colors.primary),
                ),
                contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
              ),
            ),
          ),
          const SizedBox(width: 8),
          ListenableBuilder(
            listenable: controller,
            builder: (context, _) {
              final ready = enabled && controller.text.trim().isNotEmpty;
              return AnimatedScale(
                scale: ready ? 1 : 0.9,
                duration: const Duration(milliseconds: 150),
                child: IconButton.filled(
                  key: const Key('send'),
                  tooltip: 'Send',
                  iconSize: 22,
                  style: IconButton.styleFrom(minimumSize: const Size.square(48)),
                  onPressed: ready ? onSend : null,
                  icon: const Icon(Icons.arrow_upward_rounded),
                ),
              );
            },
          ),
        ],
      ),
    );
  }
}

class _Welcome extends StatelessWidget {
  const _Welcome({required this.onSuggestion});

  final ValueChanged<String> onSuggestion;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const BrandMark(size: 64),
            const SizedBox(height: 20),
            Text('How can I help?', style: theme.textTheme.headlineSmall),
            const SizedBox(height: 8),
            Text(
              'I use what you have told me before, and say so when I do not know.',
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium?.copyWith(color: theme.colorScheme.onSurfaceVariant),
            ),
            const SizedBox(height: 24),
            for (final prompt in starterPrompts)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: OutlinedButton(
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size.fromHeight(48),
                    alignment: Alignment.centerLeft,
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                  ),
                  onPressed: () => onSuggestion(prompt),
                  child: Row(
                    children: [
                      const Icon(Icons.lightbulb_outline, size: 18),
                      const SizedBox(width: 10),
                      Expanded(child: Text(prompt)),
                    ],
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
