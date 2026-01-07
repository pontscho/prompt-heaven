---
name: p:project-docs
description: Project documentation writing standards including markdown structure, RFC/specification compliance, API documentation format, test roadmaps, implementation specifications, and conformance test suite documentation. Use when creating or updating documentation, documenting APIs, planning implementation roadmaps, or documenting protocol compliance.
---

# Project Documentation Guidelines

Guidelines for writing and maintaining technical project documentation.

## Documentation Structure

```
docs/
├── api/                    # API documentation
│   └── api-*.md           # API reference docs
├── tests/                  # Test suite documentation (optional)
│   └── category-*.md      # Conformance test categories
├── specs/                  # Specification documents (optional)
│   └── rfc*.txt           # RFC/standard specifications
├── *-roadmap.md            # Implementation roadmaps
├── *-specification.md      # Technical specifications
├── *-example.md            # Usage examples
├── *-guide.md              # User guides
└── *-compliance-plan.md    # RFC/spec compliance tracking
```

**Note**: For code analysis documentation (module/subsystem analysis), see the `code-analysis` skill.

## Documentation Types

### 1. API Documentation

**Purpose**: Document public APIs, function signatures, and usage patterns.

**Location**: `docs/api/`

**File naming**: `api-{component-name}.md`

**Structure**:
```markdown
# Component Name API

Brief description of the API purpose.

## Overview

High-level overview of the API.

## Core Types

```c
typedef struct component_t component_t;
```

## Functions

### function_name()

```c
extern return_type function_name(parameters);
```

**Description**: What the function does.

**Parameters**:
- `param1`: Description
- `param2`: Description

**Returns**: What the function returns

**Example**:
```c
// Usage example
component_t *ctx = component_create();
```

## Best Practices

- List of best practices
- Common patterns
- Things to avoid

## See Also

- Related documentation
- RFC references
```

**Examples**: `docs/api/api-{component}.md`

### 2. Specification/RFC Compliance Documentation

**Purpose**: Document RFC compliance, implementation status, and test coverage.

**Naming convention**: `{protocol}-rfc-compliance-plan.md`

**Structure**:
```markdown
# Protocol RFC Compliance Plan

## Overview

Brief description and scope.

## RFC References

### RFC XXXX - Title

- **Status**: Implemented/Partial/Planned
- **Location**: `docs/rfc/rfcXXXX.txt`
- **Key sections**: Section numbers and brief descriptions

## Implementation Status

### Implemented Features

- Feature 1 (RFC XXXX §X.X)
- Feature 2 (RFC YYYY §Y.Y)

### Partial Implementation

- Feature with limitations

### Planned Features

- Future features

## Test Coverage

### Automated Tests

- Test suite information
- Coverage metrics

### Manual Testing

- Manual test procedures

## Compliance Checklist

- [ ] RFC XXXX §X.X - Feature description
- [x] RFC YYYY §Y.Y - Completed feature
```

**Examples**: `docs/protocol-rfc-compliance-plan.md`, `docs/api-spec-compliance-plan.md`

### 3. Test Documentation

**Purpose**: Document test strategies, test suites, and test roadmaps.

**Types**:
- **Roadmaps**: `*-test-roadmap.md` - Testing strategy and plans
- **Gap analysis**: `*-gap-analysis.md` - Missing test coverage
- **Test suites**: `*-conform-test.md` - Conformance testing

**Structure for roadmaps**:
```markdown
# Component Test Roadmap

## Overview

Testing strategy and goals.

## Test Categories

### Unit Tests

- What to test
- Coverage goals

### Integration Tests

- Integration scenarios
- Test fixtures

### Conformance Tests

- Specification compliance tests
- Standard conformance test suites

## Test Implementation Plan

1. Phase 1: Core functionality
2. Phase 2: Edge cases
3. Phase 3: Performance

## Current Status

- Completed tests
- In-progress tests
- Planned tests
```

**Examples**:
- `docs/component-test-roadmap.md`
- `docs/feature-gap-analysis.md`
- `docs/protocol-conform-test.md`

### 4. Implementation Specifications

**Purpose**: Detailed technical specifications for features or components.

**Naming convention**: `{component}-specification.md`

**Structure**:
```markdown
# Component Specification

## Overview

What this component does and why.

## Requirements

### Functional Requirements

- REQ-001: Requirement description
- REQ-002: Another requirement

### Non-Functional Requirements

- Performance requirements
- Security requirements

## Architecture

High-level design and architecture.

## Implementation Details

### Data Structures

### Algorithms

### API Design

## Error Handling

How errors are handled.

## Testing Strategy

How to test the implementation.

## References

- Related RFCs
- Related documentation
```

**Examples**: `docs/component-specification.md`

### 5. Usage Examples

**Purpose**: Demonstrate how to use components or features.

**Naming convention**: `{component}-example.md`

**Structure**:
```markdown
# Component Usage Example

Brief description of what this demonstrates.

## Prerequisites

What's needed to run this example.

## Basic Usage

```c
// Simple example
```

## Advanced Usage

```c
// More complex example
```

## Common Patterns

### Pattern 1

```c
// Implementation
```

### Pattern 2

```c
// Another pattern
```

## Complete Example

Full working example with explanation.

## See Also

- Related documentation
```

**Examples**:
- `docs/simple-server-example.md`
- `docs/component-usage-example.md`

### 6. Conformance Test Suite Documentation

**Purpose**: Document conformance test suite coverage by category (e.g., protocol test suites, API validation frameworks).

**Location**: `docs/tests/` or `docs/{test-suite}/`

**Naming convention**: `category-{number}-{name}.md` or `test-{category}.md`

**Structure**:
```markdown
# Category X: Test Category Name

## Overview

Description of test category.

## Test Cases

### X.1.1 - Test Name

**Description**: What the test checks

**Status**: Pass/Fail/Partial/Not Implemented

**Notes**: Implementation notes

## Implementation Notes

Details about implementation for this category.

## References

- Specification sections
- Related documentation
```

**Examples**: Protocol conformance test suites, API validation test frameworks

## Markdown Style Guidelines

### Headers

```markdown
# Title (H1) - Only one per document
## Section (H2)
### Subsection (H3)
#### Sub-subsection (H4)
```

### Code Blocks

```markdown
```language
code here
```
```

Supported languages: `c`, `lua`, `bash`, `python`, `json`, `yaml`, `diff`

### Lists

```markdown
- Unordered list item
- Another item
  - Nested item

1. Ordered list item
2. Another item
   - Can mix with unordered
```

### Tables

```markdown
| Header 1 | Header 2 | Header 3 |
|----------|----------|----------|
| Cell 1   | Cell 2   | Cell 3   |
| Cell 4   | Cell 5   | Cell 6   |
```

### Links

```markdown
[Link text](path/to/file.md)
[RFC 6455](../rfc/rfc6455.txt)
[External link](https://example.com)
```

### Emphasis

```markdown
**Bold text**
*Italic text*
`inline code`
```

### Checkboxes (for compliance/status tracking)

```markdown
- [x] Completed item
- [ ] Incomplete item
```

## Documentation Best Practices

### Content

1. **Be concise**: Get to the point quickly
2. **Use examples**: Show, don't just tell
3. **Link references**: Reference RFCs, other docs, and source files
4. **Keep updated**: Update docs when code changes
5. **No TODO comments**: Complete documentation or create a task

### Structure

1. **Start with overview**: Brief description at the top
2. **Logical flow**: Organize content from general to specific
3. **Use headers**: Break content into scannable sections
4. **Code-first**: Show code examples before lengthy explanations
5. **Cross-reference**: Link to related documentation

### Technical Writing

1. **Active voice**: "The function returns" not "is returned by"
2. **Present tense**: "The server handles" not "will handle"
3. **Imperative for instructions**: "Use this function" not "You should use"
4. **Consistent terminology**: Use same terms throughout
5. **Avoid jargon**: Explain technical terms when first used

### Code Examples

1. **Complete examples**: Show full, working code
2. **Comment key lines**: Explain non-obvious parts
3. **Show output**: Include expected results when relevant
4. **Use real values**: Avoid placeholder like "foo", "bar"
5. **Test examples**: Ensure code examples actually work

## File Naming Conventions

| Pattern | Purpose | Example |
|---------|---------|---------|
| `api-*.md` | API documentation | `api-memory.md` |
| `{name}-example.md` | Usage examples | `server-example.md` |
| `{name}-specification.md` | Specifications | `component-specification.md` |
| `{name}-roadmap.md` | Implementation plans | `feature-test-roadmap.md` |
| `{name}-guide.md` | User guides | `user-guide.md` |
| `{protocol}-compliance-plan.md` | Spec compliance | `protocol-rfc-compliance-plan.md` |
| `category-{n}-{name}.md` | Test categories | `category-01-basics.md` |
| `test-{category}.md` | Test documentation | `test-authentication.md` |

**Note**: For analysis documentation naming (`analysis-*.md`, `subsystem-*.md`), see the `code-analysis` skill.

## References

- Code analysis documentation: See `code-analysis` skill
- Specification documents: `docs/specs/` or `docs/rfc/`
- Project overview: `PROJECT.md`

## Common Documentation Tasks

### Creating New API Documentation

1. Create new file: `docs/api/api-{component}.md`
2. Follow the API documentation structure template
3. Add complete function signatures and parameters
4. Include usage examples and best practices
5. Link from related docs

### Documenting Test Coverage

1. Create test roadmap: `docs/{component}-test-roadmap.md`
2. Document gap analysis: `docs/{component}-gap-analysis.md`
3. Link to conformance tests
4. Update as tests are implemented

### RFC Compliance Documentation

1. Create compliance plan: `docs/{protocol}-rfc-compliance-plan.md`
2. List relevant RFCs in `docs/rfc/`
3. Track implementation status
4. Link to test documentation
5. Use checkboxes for compliance items