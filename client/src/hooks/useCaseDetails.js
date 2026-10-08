import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';

const byId = (rows) => [...new Map(rows.map((row) => [row.id, row])).values()].sort((a, b) => a.id - b.id);

/**
 * Typed updates and vital sign history for one case. Updates are append-only on the
 * server, so merging by id is enough to combine fetched snapshots with pushed events.
 */
export default function useCaseDetails(caseId) {
  const [updates, setUpdates] = useState([]);
  const [error, setError] = useState(null);
  const caseRef = useRef(caseId);
  caseRef.current = caseId;

  const reload = useCallback(async () => {
    if (!caseId) return;
    try {
      const response = await axios.get(`/api/cases/${caseId}/updates`);
      if (caseRef.current === caseId) {
        setUpdates((current) => byId([...current, ...response.data]));
        setError(null);
      }
    } catch (err) {
      console.error('Error loading case updates:', err);
      if (caseRef.current === caseId) setError('Could not load case updates');
    }
  }, [caseId]);

  useEffect(() => {
    setUpdates([]);
    setError(null);
    reload();
  }, [reload]);

  const addUpdate = useCallback((update) => {
    if (update.case_id === caseRef.current) setUpdates((current) => byId([...current, update]));
  }, []);

  const vitals = updates.flatMap((update) => update.vitals || []);
  return { updates, vitals, error, reload, addUpdate };
}
