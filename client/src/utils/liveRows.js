// Cases and segments arrive both from fetched snapshots and from pushed events, in
// any order. Every row carries a server-set updated_at (fixed-width ISO-8601 UTC,
// so string comparison is chronological); keeping the newest version of each row
// means a slow snapshot can never overwrite a newer event, or vice versa.

export const upsertNewer = (rows, row) => {
  const index = rows.findIndex((r) => r.id === row.id);
  if (index === -1) return [...rows, row];
  if (rows[index].updated_at > row.updated_at) return rows;
  const next = [...rows];
  next[index] = row;
  return next;
};

// Rows missing from the snapshot are kept: they were created after it was taken.
export const mergeNewer = (rows, snapshot) => snapshot.reduce(upsertNewer, rows);
