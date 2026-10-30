# Create and manage Google Ads resources

Reporting continues to use `search_search` and `metadata_get_resource_metadata`.
Mutation tools use the same authenticated Google user and installed official
SDK version. The generic batch API supports all operations in that SDK's
`MutateOperation` message; it is not limited to search campaigns.

Call `mutations_get_mutation_schema` with no arguments to list operation types.
Follow paths such as `campaign_operation.create` or
`ad_group_ad_operation.create.ad.responsive_search_ad` to inspect fields and
enums. Schema metadata does not replace Google's business-rule validation.

## Validate and apply

Example arguments to `mutations_mutate` that create a PAUSED search campaign
with a budget and ad group in one atomic batch:

```json
{
  "customer_id": "1234567890",
  "validate_only": true,
  "operations": [
    {"campaign_budget_operation": {"create": {
      "resource_name": "customers/1234567890/campaignBudgets/-1",
      "name": "Example budget",
      "amount_micros": "10000000",
      "delivery_method": "STANDARD",
      "explicitly_shared": false
    }}},
    {"campaign_operation": {"create": {
      "resource_name": "customers/1234567890/campaigns/-2",
      "name": "Example search campaign",
      "status": "PAUSED",
      "advertising_channel_type": "SEARCH",
      "campaign_budget": "customers/1234567890/campaignBudgets/-1",
      "manual_cpc": {},
      "contains_eu_political_advertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"
    }}},
    {"ad_group_operation": {"create": {
      "name": "Example ad group",
      "campaign": "customers/1234567890/campaigns/-2",
      "type": "SEARCH_STANDARD",
      "status": "ENABLED",
      "cpc_bid_micros": "1000000"
    }}}
  ]
}
```

Replace the customer ID, budget, bidding, political-advertising declaration
and other settings with the user's actual requirements. Ten million micros
is 10 units of the account currency. Google may require additional fields or
reject a bidding strategy for a particular account. Use a Google Ads test
account for initial verification.

After successful validation, use the same operations with `validate_only:false`
to apply the authorized changes. Validation alone creates nothing and does not
reserve IDs or guarantee a later apply. The response contains real resource
names for successful mutations; negative IDs are local to one batch. Define
parents before children and use distinct temporary IDs. Ads and keywords can
reference an ad group defined earlier in the same batch.

Updates require a field mask; protobuf JSON represents it as a string:

```json
{"campaign_operation":{"update":{
  "resource_name":"customers/1234567890/campaigns/42","status":"PAUSED"
},"update_mask":"status"}}
```

Nested mask paths use lowerCamelCase, for example `networkSettings.targetSearchNetwork`.
Both snake_case and lowerCamelCase object keys are accepted. Unknown fields,
unknown enum names, and conflicting create/update/remove choices are rejected
before the network call. Removal uses the resource name directly:

```json
{"ad_group_criterion_operation":{"remove":"customers/1234567890/adGroupCriteria/123~456"}}
```

## Results, manager accounts and failure handling

- `customer_id` is always the target Ads account. For manager access, also pass
  `login_customer_id`. Google checks the signed-in user's permissions; supplying
  an ID does not grant access. Hosted deployments must not use shared ADC.
- `partial_failure` defaults to `false`, so a failing operation rejects the
  entire atomic batch. For independent operations only, opt into `true` and
  inspect `response.partial_failure_error.details` for indexed Google Ads
  errors. Empty result entries retain their original positions. A successful
  MCP call with partial failure does not mean every operation succeeded.
- Top-level Google Ads failures return an MCP tool error containing the Google
  request ID and structured failure details. They do not report success.
- There are no automatic mutation retries. After a timeout or connection loss,
  search the account before repeating a create: the first call may have applied.
- The MCP metadata marks mutation calls writable, potentially destructive and
  non-idempotent, including validation calls. Account/budget/launch decisions
  belong to the user's instructions. New campaigns should remain PAUSED until
  launch is authorized.

The unit tests use real SDK messages with mocked network calls, including a
linked budget/campaign/ad-group/responsive-search-ad/keyword batch. They do not
verify Google's live account policies, OAuth consent configuration, or billing.

References: [Google batch mutations](https://developers.google.com/google-ads/api/docs/mutating/overview),
[mutation best practices](https://developers.google.com/google-ads/api/docs/mutating/best-practices).
