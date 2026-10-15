import { useCallback, useRef, useState } from 'react';
import axios from 'axios';

const newId = () => (window.crypto?.randomUUID
  ? window.crypto.randomUUID()
  : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`);

/** A sent report can be retried with exactly the same identity and final clip count. */
export default function useReportDelivery() {
  const current = useRef(null);
  const inFlight = useRef(null);
  const [pending, setPending] = useState(null);
  const [error, setError] = useState(null);

  const keep = useCallback((report) => {
    current.current = report;
    setPending(report);
    setError(null);
    return report;
  }, []);
  const clear = useCallback(() => keep(null), [keep]);
  const begin = (caseId) => {
    if (current.current) return null;
    return keep({ caseId, id: newId(), clips: 0 });
  };
  const send = useCallback(() => {
    if (inFlight.current) return inFlight.current;
    const report = current.current;
    if (!report) return Promise.resolve(true);
    const attempt = async () => {
      try {
        await axios.post(`/api/cases/${report.caseId}/reports/${report.id}/end`,
          { clips: report.clips }, { timeout: 15000 });
        if (current.current === report) clear();
        return true;
      } catch (failure) {
        if (current.current === report) setError(failure.response?.data?.error ||
          'Report not sent. Retry Send when the connection is back.');
        return false;
      }
    };
    inFlight.current = attempt().finally(() => { inFlight.current = null; });
    return inFlight.current;
  }, [clear]);

  return { pending, error, begin, keep, clear, send };
}
