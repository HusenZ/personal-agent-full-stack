/// Short, local times for lists and messages, without a localisation package.
String formatTimestamp(DateTime value, {DateTime? now}) {
  final local = value.toLocal();
  final today = (now ?? DateTime.now()).toLocal();
  final time = '${_two(local.hour)}:${_two(local.minute)}';
  if (local.year == today.year && local.month == today.month && local.day == today.day) {
    return time;
  }
  final yesterday = today.subtract(const Duration(days: 1));
  if (local.year == yesterday.year && local.month == yesterday.month && local.day == yesterday.day) {
    return 'Yesterday';
  }
  final month = _months[local.month - 1];
  return local.year == today.year ? '$month ${local.day}' : '$month ${local.day}, ${local.year}';
}

/// Which heading a conversation falls under in a list sorted newest first.
String recencyGroup(DateTime value, {DateTime? now}) {
  final local = value.toLocal();
  final today = _dateOnly((now ?? DateTime.now()).toLocal());
  final days = today.difference(_dateOnly(local)).inDays;
  if (days <= 0) {
    return 'Today';
  }
  if (days == 1) {
    return 'Yesterday';
  }
  if (days < 7) {
    return 'Previous 7 days';
  }
  if (days < 30) {
    return 'Previous 30 days';
  }
  return 'Older';
}

/// A heading between messages from different days: "Today", "Yesterday" or a date.
String dayLabel(DateTime value, {DateTime? now}) {
  final local = value.toLocal();
  final today = _dateOnly((now ?? DateTime.now()).toLocal());
  final days = today.difference(_dateOnly(local)).inDays;
  if (days == 0) {
    return 'Today';
  }
  if (days == 1) {
    return 'Yesterday';
  }
  final month = _months[local.month - 1];
  return local.year == today.year ? '$month ${local.day}' : '$month ${local.day}, ${local.year}';
}

bool isSameDay(DateTime a, DateTime b) => _dateOnly(a.toLocal()) == _dateOnly(b.toLocal());

/// "Good morning" and so on, by the local hour.
String greetingFor(DateTime now) => switch (now.hour) {
  < 5 => 'Good evening',
  < 12 => 'Good morning',
  < 18 => 'Good afternoon',
  _ => 'Good evening',
};

/// "Saturday, 3 October".
String longDate(DateTime value) => '${_weekdays[value.weekday - 1]}, ${value.day} ${_monthNames[value.month - 1]}';

/// "temporary_context" → "Temporary context".
String humanise(String value) {
  final words = value.replaceAll('_', ' ');
  return words.isEmpty ? words : words[0].toUpperCase() + words.substring(1);
}

// Calendar dates without times, so day differences ignore daylight saving.
DateTime _dateOnly(DateTime value) => DateTime.utc(value.year, value.month, value.day);

String _two(int value) => value.toString().padLeft(2, '0');

const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const _weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

const _monthNames = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
];
