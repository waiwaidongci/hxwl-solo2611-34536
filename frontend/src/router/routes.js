export const pages = ['overview', 'inputs', 'run', 'results', 'review', 'delivery'];

export function route() {
  const [page, id, offset] = location.hash.slice(1).split('/');
  const numericId = Number(id);
  const parsed = {page:pages.includes(page) ? page : 'overview',
                  id:Number.isNaN(numericId) ? null : numericId,
                  offset:Math.max(0, Number(offset) || 0)};
  if (page === 'inputs') {
    // #inputs/finance[/id[/offset]] versus #inputs[/id[/offset]]
    parsed.kind = id && Number.isNaN(Number(id)) ? id : 'checkout';
  }
  return parsed;
}
