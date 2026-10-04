import 'package:flutter_test/flutter_test.dart';
import 'package:personal_intelligence/core/ui/formatting.dart';

void main() {
  final now = DateTime(2026, 10, 3, 9);

  test('conversations are grouped by how recent they are', () {
    expect(recencyGroup(DateTime(2026, 10, 3, 0, 5), now: now), 'Today');
    expect(recencyGroup(DateTime(2026, 10, 2, 23, 59), now: now), 'Yesterday');
    expect(recencyGroup(DateTime(2026, 9, 28), now: now), 'Previous 7 days');
    expect(recencyGroup(DateTime(2026, 9, 10), now: now), 'Previous 30 days');
    expect(recencyGroup(DateTime(2025, 1, 1), now: now), 'Older');
  });

  test('messages are separated by day', () {
    expect(dayLabel(DateTime(2026, 10, 3, 1), now: now), 'Today');
    expect(dayLabel(DateTime(2026, 10, 2, 1), now: now), 'Yesterday');
    expect(dayLabel(DateTime(2026, 8, 14), now: now), 'Aug 14');
    expect(dayLabel(DateTime(2025, 8, 14), now: now), 'Aug 14, 2025');
    expect(isSameDay(DateTime(2026, 10, 3, 0, 1), DateTime(2026, 10, 3, 23, 59)), isTrue);
  });

  test('the greeting follows the time of day', () {
    expect(greetingFor(DateTime(2026, 10, 3, 7)), 'Good morning');
    expect(greetingFor(DateTime(2026, 10, 3, 13)), 'Good afternoon');
    expect(greetingFor(DateTime(2026, 10, 3, 21)), 'Good evening');
    expect(greetingFor(DateTime(2026, 10, 3, 2)), 'Good evening');
    expect(longDate(DateTime(2026, 10, 3)), 'Saturday, 3 October');
  });
}
