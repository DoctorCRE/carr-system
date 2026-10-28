-- Expose only email filing history needed for undo and suppression.
create view v_email_thread_filing_event as
select id, subject_id, verb, new_value, recorded_at, mutation_order
from event
where verb in ('inherit-email-thread', 'confirm-email-filing', 'undo-email-thread-filing');
grant select on v_email_thread_filing_event to carr_reader, carr_writer;
