import 'package:flutter/material.dart';

/// The app's look, in one place, so screens use the theme rather than
/// styling each widget by hand.
abstract final class AppTheme {
  static const seed = Color(0xFF5B5BD6);

  static ThemeData light() => _build(ColorScheme.fromSeed(seedColor: seed));

  static ThemeData dark() => _build(ColorScheme.fromSeed(seedColor: seed, brightness: Brightness.dark));

  static ThemeData _build(ColorScheme colors) {
    final base = ThemeData(colorScheme: colors, useMaterial3: true);
    final text = base.textTheme;
    const rounded = RoundedRectangleBorder(borderRadius: BorderRadius.all(Radius.circular(16)));

    return base.copyWith(
      scaffoldBackgroundColor: colors.surface,
      textTheme: text.copyWith(
        headlineMedium: text.headlineMedium?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -0.5),
        headlineSmall: text.headlineSmall?.copyWith(fontWeight: FontWeight.w700, letterSpacing: -0.3),
        titleLarge: text.titleLarge?.copyWith(fontWeight: FontWeight.w700),
        titleMedium: text.titleMedium?.copyWith(fontWeight: FontWeight.w600),
      ),
      appBarTheme: AppBarTheme(
        centerTitle: false,
        backgroundColor: colors.surface,
        surfaceTintColor: colors.surfaceTint,
        scrolledUnderElevation: 2,
        titleTextStyle: text.titleLarge?.copyWith(fontSize: 22, fontWeight: FontWeight.w700, color: colors.onSurface),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: colors.surfaceContainerHighest.withValues(alpha: 0.5),
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide.none),
        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide.none),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(14),
          borderSide: BorderSide(color: colors.primary, width: 1.5),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(14),
          borderSide: BorderSide(color: colors.error),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(64, 52),
          shape: rounded,
          textStyle: text.titleMedium?.copyWith(fontWeight: FontWeight.w600),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(minimumSize: const Size(0, 48), shape: rounded),
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        margin: EdgeInsets.zero,
        color: colors.surfaceContainerLow,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(20),
          side: BorderSide(color: colors.outlineVariant.withValues(alpha: 0.6)),
        ),
      ),
      chipTheme: base.chipTheme.copyWith(
        shape: const StadiumBorder(),
        side: BorderSide(color: colors.outlineVariant),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        shape: rounded,
        backgroundColor: colors.inverseSurface,
        contentTextStyle: text.bodyMedium?.copyWith(color: colors.onInverseSurface),
      ),
      dialogTheme: const DialogThemeData(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.all(Radius.circular(24))),
      ),
      bottomSheetTheme: const BottomSheetThemeData(showDragHandle: true),
      navigationBarTheme: NavigationBarThemeData(
        height: 68,
        backgroundColor: colors.surfaceContainer,
        indicatorColor: colors.primaryContainer,
        labelTextStyle: WidgetStatePropertyAll(text.labelMedium?.copyWith(fontWeight: FontWeight.w600)),
      ),
      listTileTheme: const ListTileThemeData(contentPadding: EdgeInsets.symmetric(horizontal: 20)),
      dividerTheme: DividerThemeData(color: colors.outlineVariant.withValues(alpha: 0.5), space: 1),
    );
  }
}
