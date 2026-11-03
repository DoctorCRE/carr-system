export const title = 'Competing signatures remain held across scans';
export const state = v => v.findings.length > 1 ? 'Duplicate proposal recorded' : v.badBinding ? 'Changed evidence submitted' : v.badMerge ? 'Competing signature guessed' : v.phase === 'competing' ? 'Original proposal retained' : v.phase === 'resolve' ? 'Checking returning signature' : v.excluded.length ? 'Distinct sources proven' : 'Competing signatures held';
export const step = a => ({Compete:'Read two indistinguishable keyed signatures', Scan:'Read one signature again', Resolve:'Check retained evidence', Prove:'Read distinguishing evidence for the other source', Restart:'Restart intake'})[a];
export const readerActions = [];
