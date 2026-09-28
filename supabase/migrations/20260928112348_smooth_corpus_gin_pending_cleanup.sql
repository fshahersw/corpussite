-- Measured 518 pending search-index pages required 10.036 seconds to merge,
-- exceeding the existing eight-second PostgREST timeout even for one new row.
-- Bound each foreground cleanup to a smaller backlog. This preserves the index,
-- search semantics, rows, permissions and the existing API timeout.
set local lock_timeout = '2s';
alter index public.corpus_records_search_idx set (gin_pending_list_limit = 512);

-- Rollback, if later measurements warrant it:
-- alter index public.corpus_records_search_idx reset (gin_pending_list_limit);
