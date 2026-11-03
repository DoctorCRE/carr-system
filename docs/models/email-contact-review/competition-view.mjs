export const title = 'Competing signatures remain held across scans';
export const state = v => v.findings.length > 1 ? 'Duplicate proposal recorded' : v.badBinding ? 'Changed evidence submitted' : v.badMerge ? 'Competing signature guessed' : v.phase === 'competing' ? 'Original proposal retained' : 'Competing signatures held';
export const step = a => ({Compete:'Read two indistinguishable keyed signatures', Scan:'Read one signature again', Resolve:'Check retained evidence', Restart:'Restart intake'})[a];
export const readerActions = [];
