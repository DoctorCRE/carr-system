export const state = v => v.target + (v.detail ? ': card visible' : ': no private detail') + (v.actions ? '; review available' : '; no operations');
export const step = name => ({ReadCurrent:'Owner opens current source',ReadForeign:'Other partner opens source',ReadMissing:'Unknown or deleted source',ReadChanged:'Source changes',ReadRetired:'Source retires',ReadReviewed:'Review completed',ReadZeroDecisions:'No decisions remain'})[name] ?? name;
export const readerActions = ['ReadCurrent','ReadForeign','ReadMissing','ReadChanged','ReadRetired','ReadReviewed'];
