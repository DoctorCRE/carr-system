export const regions = ["session","card"];
const LABELS = {
	"SignOut": "Sign out",
	"SignIn": "Sign in",
	"IssueCurrentProposal": "Issue current proposal",
	"ResolveOwningSession": "Resolve owning session",
	"ResolveForeignOrSignedOut": "Resolve foreign or signed out",
	"ResolveAgainWithoutMutation": "Resolve again without mutation",
	"EditOrRetireSource": "Edit or retire source",
	"ExplicitDomainApproval": "Explicit domain approval",
	"ResolveOrActAgain": "Resolve or act again",
	"ResolveOrActStaleReference": "Resolve or act stale reference",
	"ResolveFreshEligibleReference": "Resolve fresh eligible reference"
};

export const readerActions = [];
export const state = (v) => `${v.session === "SignedIn" ? "Signed in" : "Signed out"}: ${{Unissued:"No link",Issued:"Link available",Review:"Draft ready for review",Expired:"Link expired",Consumed:"Draft approved",Refused:"Request refused"}[v.card]}`;
export const step = (action) => LABELS[action] ?? action;
