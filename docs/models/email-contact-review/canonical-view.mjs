export const title = 'Two signatures sharing a Message-ID';
export const state = v => v.findings.length > 2 ? 'Duplicate proposals recorded' : v.badMerge ? 'Ambiguous source merged' : v.badBinding ? 'Changed evidence submitted' : v.phase === 'resolve' ? 'Checking signature source' : 'Frozen proposals retained';
export const step = a => ({Scan:'Read another mail representation', Resolve:'Resolve source and hold or record', Retry:'Retry after lost receipt', Restart:'Restart intake'})[a];
export const readerActions = [];
