import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

/// The signed-in layout: chats, memories and profile, each keeping its own
/// navigation stack when the user switches between them.
class HomeShell extends StatelessWidget {
  const HomeShell({super.key, required this.shell});

  final StatefulNavigationShell shell;

  @override
  Widget build(BuildContext context) => Scaffold(
    body: shell,
    bottomNavigationBar: NavigationBar(
      selectedIndex: shell.currentIndex,
      onDestinationSelected: (index) => shell.goBranch(index, initialLocation: index == shell.currentIndex),
      destinations: const [
        NavigationDestination(icon: Icon(Icons.wb_sunny_outlined), selectedIcon: Icon(Icons.wb_sunny), label: 'Today'),
        NavigationDestination(icon: Icon(Icons.forum_outlined), selectedIcon: Icon(Icons.forum), label: 'Chats'),
        NavigationDestination(
          icon: Icon(Icons.psychology_outlined),
          selectedIcon: Icon(Icons.psychology),
          label: 'Memory',
        ),
        NavigationDestination(icon: Icon(Icons.person_outline), selectedIcon: Icon(Icons.person), label: 'You'),
      ],
    ),
  );
}
