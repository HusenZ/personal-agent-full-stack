import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_client.dart';
import '../../core/api/api_exception.dart';
import '../../core/providers.dart';

enum GoalPriority { low, medium, high }

class Goal {
  const Goal(this.name, this.priority);

  final String name;
  final GoalPriority priority;
}

class Profile {
  const Profile({
    this.timezone = 'UTC',
    this.goals = const [],
    this.constraints = const [],
    this.preferences = const {},
  });

  factory Profile.fromJson(Map<String, dynamic> json) => Profile(
    timezone: json['timezone'] as String,
    goals: [
      for (final goal in json['goals'] as List<dynamic>)
        Goal((goal as Map<String, dynamic>)['name'] as String, GoalPriority.values.byName(goal['priority'] as String)),
    ],
    constraints: [for (final item in json['constraints'] as List<dynamic>) item as String],
    preferences: {
      for (final entry in (json['preferences'] as Map<String, dynamic>).entries) entry.key: entry.value as String,
    },
  );

  final String timezone;
  final List<Goal> goals;
  final List<String> constraints;
  final Map<String, String> preferences;

  bool get isEmpty => goals.isEmpty && constraints.isEmpty && preferences.isEmpty;
}

class ProfileRepository {
  ProfileRepository(this._api);

  final ApiClient _api;

  /// The profile, or an empty one for someone who has not built theirs yet.
  Future<Profile> load() async {
    try {
      return Profile.fromJson((await _api.get('/api/v1/profile')).object);
    } on ApiException catch (error) {
      if (error.code == 'PROFILE_NOT_FOUND') {
        return const Profile();
      }
      rethrow;
    }
  }

  /// Replaces every collection with what is given, as the API defines.
  Future<Profile> save(Profile profile) async => Profile.fromJson(
    (await _api.patch(
      '/api/v1/profile',
      body: {
        'timezone': profile.timezone,
        'goals': [
          for (final goal in profile.goals) {'name': goal.name, 'priority': goal.priority.name},
        ],
        'constraints': profile.constraints,
        'preferences': profile.preferences,
      },
    )).object,
  );
}

final profileRepositoryProvider = Provider<ProfileRepository>((ref) => ProfileRepository(ref.watch(apiClientProvider)));

final profileProvider = FutureProvider.autoDispose<Profile>((ref) => ref.watch(profileRepositoryProvider).load());
