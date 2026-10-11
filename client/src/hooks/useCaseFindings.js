import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import toast from 'react-hot-toast';

/**
 * Patient information extracted from one case's reports (GET /api/cases/:id/findings), kept
 * current by `findings.updated` events, which carry the case's full set. Each fact says which
 * radio clip or typed note it came from.
 */
export default function useCaseFindings(caseId) {
  const [findings, setFindings] = useState(null);
  const caseRef = useRef(caseId);
  caseRef.current = caseId;

  const reload = useCallback(async () => {
    if (!caseId) return;
    try {
      const response = await axios.get(`/api/cases/${caseId}/findings`);
      if (caseRef.current === caseId) setFindings(response.data);
    } catch (error) {
      console.error('Error loading patient information:', error);
    }
  }, [caseId]);

  useEffect(() => {
    setFindings(null);
    reload();
  }, [reload]);

  const apply = useCallback((data) => {
    if (data.case_id === caseRef.current) setFindings(data.findings);
  }, []);

  const retry = useCallback(async () => {
    try {
      const response = await axios.post(`/api/cases/${caseId}/findings/retry`);
      if (caseRef.current === caseId) setFindings(response.data);
    } catch (error) {
      toast.error(error.response?.data?.error || 'Could not retry');
    }
  }, [caseId]);

  return { findings, reload, apply, retry };
}
