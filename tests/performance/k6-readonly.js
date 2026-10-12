import http from 'k6/http';
import { check } from 'k6';

const baseURL = (__ENV.STAGING_BASE_URL || '').replace(/\/$/, '');
const p95Milliseconds = Number(__ENV.STAGING_P95_MS || 0);
const maximumErrorRate = Number(__ENV.STAGING_MAX_ERROR_RATE || -1);

if (!baseURL || !/^https:\/\//.test(baseURL)) {
  throw new Error('STAGING_BASE_URL must be an HTTPS disposable staging URL');
}
if (!(p95Milliseconds > 0) || !(maximumErrorRate >= 0 && maximumErrorRate < 1)) {
  throw new Error('Set measured STAGING_P95_MS and STAGING_MAX_ERROR_RATE for this staging profile');
}

export const options = {
  scenarios: {
    readonly_public_pages: {
      executor: 'ramping-vus',
      stages: [
        { duration: '2m', target: 10 },
        { duration: '3m', target: 50 },
        { duration: '3m', target: 100 },
        { duration: '1m', target: 0 },
      ],
      gracefulRampDown: '30s',
    },
  },
  thresholds: {
    http_req_failed: [`rate<${maximumErrorRate}`],
    http_req_duration: [`p(95)<${p95Milliseconds}`],
  },
};

export default function () {
  for (const path of ['/login', '/documentation']) {
    const response = http.get(`${baseURL}${path}`, {
      tags: { route: path },
      redirects: 0,
    });
    check(response, {
      [`${path} is HTTP 200`]: result => result.status === 200,
      [`${path} contains no server error`]: result => !result.body.includes('Внутренняя ошибка'),
    });
  }
}
