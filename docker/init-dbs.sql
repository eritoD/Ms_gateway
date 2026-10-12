-- Se ejecuta solo la primera vez que se crea el volumen de PostgreSQL.
CREATE ROLE sportmach_users LOGIN PASSWORD 'UsersLocal2026';
CREATE ROLE sportmach_matching LOGIN PASSWORD 'MatchingLocal2026';
CREATE ROLE sportmach_activities LOGIN PASSWORD 'ActivitiesLocal2026';
CREATE DATABASE sportmach_users OWNER sportmach_users;
CREATE DATABASE sportmach_matching OWNER sportmach_matching;
CREATE DATABASE sportmach_activities OWNER sportmach_activities;
