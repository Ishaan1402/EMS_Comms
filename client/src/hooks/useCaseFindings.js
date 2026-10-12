import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import toast from 'react-hot-toast';

/**
 * Patient information extracted from one case's reports (GET /api/cases/:id/findings), kept
 * current by `findings.updated` events, which carry the case's full set. Each fact says which
 * radio clip or typed note it came from.
 *
 * A fetch can be answered after a newer pushed snapshot (or another fetch); only a snapshot for
 * the case on screen with a later server as_of replaces what is shown.
 */
export default function useCaseFindings(caseId) {
  const [findings, setFindings] = useState(null);
  const caseRef = useRef(caseId);
  caseRef.current = caseId;
  const shown = useRef(null);

  const show = useCallback((snapshot) => {
    if (snapshot.case_id !== caseRef.current) return;
    if (shown.current && shown.current.case_id === snapshot.case_id && shown.current.as_of >= snapshot.as_of) return;
    shown.current = snapshot;
    setFindings(snapshot);
  }, []);

  const reload = useCallback(async () => {
    if (!caseId) return;
    try {
      show((await axios.get(`/api/cases/${caseId}/findings`)).data);
    } catch (error) {
      console.error('Error loading patient information:', error);
    }
  }, [caseId, show]);

  useEffect(() => {
    shown.current = null;
    setFindings(null);
    reload();
  }, [reload]);

  const apply = useCallback((data) => show(data.findings), [show]);

  const retry = useCallback(async () => {
    try {
      show((await axios.post(`/api/cases/${caseId}/findings/retry`)).data);
    } catch (error) {
      toast.error(error.response?.data?.error || 'Could not retry');
    }
  }, [caseId, show]);

  return { findings, reload, apply, retry };
}
