-- Ясна: проект физической схемы PostgreSQL.
-- Самостоятельный DDL для обсуждения/проверки в ПУСТОЙ базе.
-- В Django создавать эту структуру моделями и миграциями, не запускать оба пути.
BEGIN;
CREATE TABLE family (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 contact_name varchar(160) NOT NULL,
 phone varchar(32) NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE child (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 family_id bigint NOT NULL REFERENCES family(id) ON DELETE RESTRICT,
 name varchar(160) NOT NULL,
 active boolean NOT NULL DEFAULT true
);
CREATE INDEX child_family_idx ON child(family_id);
CREATE TABLE access_link (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 family_id bigint NOT NULL REFERENCES family(id) ON DELETE RESTRICT,
 token_hash varchar(64) NOT NULL UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL,
 revoked_at timestamptz,
 CHECK (expires_at > created_at)
);
CREATE INDEX access_link_family_idx ON access_link(family_id);
CREATE TABLE lesson_policy (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 name varchar(120) NOT NULL,
 free_cancel_hours integer NOT NULL CHECK (free_cancel_hours >= 0),
 charge_absence boolean NOT NULL DEFAULT true,
 charge_late_cancel boolean NOT NULL DEFAULT true
);
CREATE TABLE lesson (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 policy_id bigint NOT NULL REFERENCES lesson_policy(id) ON DELETE RESTRICT,
 title varchar(160) NOT NULL,
 kind varchar(16) NOT NULL CHECK (kind IN ('group','individual')),
 starts_at timestamptz NOT NULL,
 ends_at timestamptz NOT NULL,
 capacity integer NOT NULL CHECK (capacity > 0),
 status varchar(16) NOT NULL DEFAULT 'scheduled'
   CHECK (status IN ('scheduled','completed','cancelled')),
 CHECK (ends_at > starts_at),
 CHECK (kind <> 'individual' OR capacity = 1)
);
CREATE INDEX lesson_starts_idx ON lesson(starts_at);
CREATE INDEX lesson_policy_idx ON lesson(policy_id);
CREATE TABLE booking (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 lesson_id bigint NOT NULL REFERENCES lesson(id) ON DELETE RESTRICT,
 child_id bigint NOT NULL REFERENCES child(id) ON DELETE RESTRICT,
 status varchar(16) NOT NULL DEFAULT 'booked'
   CHECK (status IN ('booked','cancelled')),
 attendance varchar(16) NOT NULL DEFAULT 'pending'
   CHECK (attendance IN ('pending','present','absent','cancelled')),
 created_at timestamptz NOT NULL DEFAULT now(),
 cancelled_at timestamptz,
 UNIQUE (lesson_id, child_id)
);
CREATE INDEX booking_child_idx ON booking(child_id);
CREATE TABLE pass_plan (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 name varchar(120) NOT NULL,
 lessons integer NOT NULL CHECK (lessons > 0),
 validity_days integer NOT NULL CHECK (validity_days > 0),
 price numeric(12,2) NOT NULL CHECK (price >= 0),
 active boolean NOT NULL DEFAULT true
);
-- Счет фиксирует условия покупки: изменение тарифа не меняет старый счет.
-- Разовое занятие продается тарифом на одно занятие.
CREATE TABLE invoice (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 child_id bigint NOT NULL REFERENCES child(id) ON DELETE RESTRICT,
 plan_id bigint NOT NULL REFERENCES pass_plan(id) ON DELETE RESTRICT,
 amount numeric(12,2) NOT NULL CHECK (amount >= 0),
 currency varchar(3) NOT NULL DEFAULT 'RUB' CHECK (currency = 'RUB'),
 lessons integer NOT NULL CHECK (lessons > 0),
 validity_days integer NOT NULL CHECK (validity_days > 0),
 status varchar(16) NOT NULL DEFAULT 'open' CHECK (status IN ('open','paid','cancelled')),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX invoice_child_idx ON invoice(child_id);
CREATE INDEX invoice_plan_idx ON invoice(plan_id);
CREATE TABLE payment (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 invoice_id bigint NOT NULL REFERENCES invoice(id) ON DELETE RESTRICT,
 method varchar(16) NOT NULL CHECK (method IN ('yookassa','cash','transfer')),
 provider_payment_id varchar(128) UNIQUE,
 operation_key uuid NOT NULL UNIQUE,
 amount numeric(12,2) NOT NULL CHECK (amount > 0),
 status varchar(16) NOT NULL DEFAULT 'pending'
   CHECK (status IN ('pending','succeeded','cancelled')),
 created_at timestamptz NOT NULL DEFAULT now(),
 confirmed_at timestamptz,
 CHECK (status <> 'succeeded' OR confirmed_at IS NOT NULL)
);
CREATE INDEX payment_invoice_idx ON payment(invoice_id);
CREATE TABLE subscription (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 invoice_id bigint NOT NULL UNIQUE REFERENCES invoice(id) ON DELETE RESTRICT,
 starts_on date NOT NULL,
 expires_on date NOT NULL,
 CHECK (expires_on >= starts_on)
);
-- Остаток = SUM(delta). Выдача +N, посещение -1, исправление +1.
CREATE TABLE lesson_entry (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 subscription_id bigint NOT NULL REFERENCES subscription(id) ON DELETE RESTRICT,
 booking_id bigint REFERENCES booking(id) ON DELETE RESTRICT,
 delta integer NOT NULL CHECK (delta <> 0),
 reason varchar(240) NOT NULL,
 operation_key uuid NOT NULL UNIQUE,
 reverses_id bigint UNIQUE REFERENCES lesson_entry(id) ON DELETE RESTRICT,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX lesson_entry_subscription_idx ON lesson_entry(subscription_id);
CREATE INDEX lesson_entry_booking_idx ON lesson_entry(booking_id);
CREATE TABLE receipt (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 payment_id bigint NOT NULL REFERENCES payment(id) ON DELETE RESTRICT,
 file_key text,
 external_url text,
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK ((file_key IS NOT NULL) <> (external_url IS NOT NULL))
);
CREATE INDEX receipt_payment_idx ON receipt(payment_id);
CREATE TABLE notification (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 family_id bigint NOT NULL REFERENCES family(id) ON DELETE RESTRICT,
 text text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 read_at timestamptz
);
CREATE INDEX notification_family_idx ON notification(family_id, created_at);
CREATE TABLE audit_event (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 actor_label varchar(160) NOT NULL,
 entity_type varchar(64) NOT NULL,
 entity_id bigint NOT NULL,
 before_data jsonb,
 after_data jsonb NOT NULL,
 reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_entity_idx ON audit_event(entity_type, entity_id, created_at);
COMMIT;
