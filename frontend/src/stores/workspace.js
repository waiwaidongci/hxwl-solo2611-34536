import {request, saveFile} from '../api/http.js';
import {session} from './session.js';

const call = (path, body) => request(path, {token:session.token, body});
export const workspace = {
  spec: null,
  datasets: [],
  financeDatasets: [],
  runs: [],
  async init() { this.spec = await request('/spec'); },
  async refresh() {
    const [datasets, financeDatasets, runs] = await Promise.all([
      call('/datasets?kind=checkout'), call('/datasets?kind=finance'), call('/runs'),
    ]);
    this.datasets = datasets;
    this.financeDatasets = financeDatasets;
    this.runs = runs;
  },
  getDataset: (id, offset = 0) => call(`/datasets/${id}?offset=${offset}`),
  getRun: id => call(`/runs/${id}`),
  upload: data => call('/datasets', data),
  start: data => call('/runs', data),
  review: (id, data) => call(`/runs/${id}/review`, data),
  audit: () => call('/audit'),
  schedules: () => call('/schedules'),
  deliveries: () => call('/deliveries'),
  schedule: data => call('/schedules', data),
  disable: id => call(`/schedules/${id}/disable`, {}),
  download: (path, filename) => saveFile(path, session.token, filename),
};
