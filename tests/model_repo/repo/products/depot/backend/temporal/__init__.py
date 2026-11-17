from products.depot.backend.temporal.activities import count_bins
from products.depot.backend.temporal.audit import AUDIT_WORKFLOWS
from products.depot.backend.temporal.restock import RestockWorkflow

WORKFLOWS = [RestockWorkflow, *AUDIT_WORKFLOWS]
ACTIVITIES = [count_bins]
