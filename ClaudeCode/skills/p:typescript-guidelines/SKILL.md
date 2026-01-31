---
name: p:typescript-guidelines
description: TypeScript coding guidelines and best practices including type definitions, memory management patterns, code style (indentation, naming), JSDoc documentation, and module templates. Use when writing TypeScript files, creating type definitions, or following project TypeScript conventions.
---

## Skill: p:typescript-guidelines

**Base directory**: ~/.claude/skills/p:typescript

### 0. Code Implementation Guidelines

Follow these rules when you write TypeScript code:

- Use early returns whenever possible to make the code more readable
- When ID is part of a variable the expected naming is 'Id' (e.g., motivationId, userId)
- Prefer `const` over `let` when variables don't need reassignment
- Use `readonly` for class properties that shouldn't be modified after initialization
- Use TAB for indentation, DON'T USE SPACE for it
- Keep code style of the source code in the current file
- Always use explicit return types for public functions

### 1. Code Documentation Guidelines

- Use JSDoc format for documenting classes, interfaces, and functions
- Always use @ for JSDoc tags (@param, @returns, @throws, etc.)
- For function documentation use /**
- For interface/class documentation use /**
- For complex types, add JSDoc comments explaining the purpose

Example:
```typescript
/**
 * Calculates the total price including tax.
 *
 * @param price - The base price of the item
 * @param taxRate - The tax rate as a decimal (e.g., 0.20 for 20%)
 *
 * @returns The total price including tax
 *
 * @throws {Error} When price is negative
 */
function calculateTotal(price: number, taxRate: number): number {
    if (price < 0) {
        throw new Error('Price cannot be negative');
    }
    return price * (1 + taxRate);
}
```

### 2. Type System Guidelines

#### Type Definitions
- Use `interface` for object shapes that describe data structures
- Use `type` for unions, intersections, and complex type manipulations
- Prefer `unknown` over `any` when the type is truly unknown
- Use strict null checks - always handle null/undefined cases

Example:
```typescript
// Good - Interface for data structures
interface User {
    id: number;
    name: string;
    email: string;
    readonly createdAt: Date;
}

// Good - Type for unions
type Status = 'pending' | 'active' | 'inactive';
type ID = string | number;

// Good - Type for complex operations
type DeepPartial<T> = {
    [P in keyof T]?: T[P] extends object ? DeepPartial<T[P]> : T[P];
};
```

#### Type Guards
- Use type guards to narrow types safely
- Prefer `instanceof` for class instances
- Use `typeof` for primitive types
- Create custom type guard functions for complex checks

Example:
```typescript
function isString(value: unknown): value is string {
    return typeof value === 'string';
}

function processValue(value: string | number): string {
    if (isString(value)) {
        return value.toUpperCase();
    }
    return value.toString();
}
```

### 3. Memory Management & Performance

#### General Principles
- Avoid memory leaks in event listeners and subscriptions
- Use weak references (WeakMap, WeakSet) for caching when appropriate
- Clean up resources in useEffect return functions (React) or dispose methods
- Be mindful of closure memory retention

#### Best Practices
- **Unsubscribe from observables**: Always clean up subscriptions
- **Remove event listeners**: Use `{ once: true }` or manual cleanup
- **Avoid global variables**: Prefer module-level or function-scoped variables
- **Use appropriate data structures**: Map/Set vs Object/Array based on use case

Example:
```typescript
class EventManager {
    private listeners = new Map<string, Set<() => void>>();

    on(event: string, callback: () => void): () => void {
        if (!this.listeners.has(event)) {
            this.listeners.set(event, new Set());
        }
        this.listeners.get(event)!.add(callback);

        // Return unsubscribe function
        return () => {
            this.listeners.get(event)?.delete(callback);
        };
    }

    cleanup(): void {
        this.listeners.clear();
    }
}
```

#### Performance Optimization
- Use memoization for expensive calculations
- Lazy load heavy modules
- Prefer `const` assertions for literal types
- Use `as const` for tuple inference
- Leverage TypeScript's strict mode for better performance hints

### 4. Code Style & Standards

#### Naming Conventions
- PascalCase for: classes, interfaces, types, enums, React components
- camelCase for: variables, functions, methods, properties
- SCREAMING_SNAKE_CASE for: constants, enum values
- Prefix interfaces with 'I' only if required by project convention (generally avoid)

Examples:
```typescript
// PascalCase for types
interface UserConfig { }
type ApiResponse<T> = { }
enum HttpStatus { OK = 200, NOT_FOUND = 404 }

// camelCase for variables/functions
const userName = 'John';
function fetchUserData(): Promise<User> { }

// SCREAMING_SNAKE_CASE for constants
const MAX_RETRY_COUNT = 3;
const API_BASE_URL = 'https://api.example.com';
```

#### Code Organization
- Group imports: external libraries, internal modules, types, styles
- Sort imports alphabetically within groups
- Use absolute imports for cross-module dependencies
- Keep functions small and focused (single responsibility)
- Maximum line length: 100-120 characters
- Use early returns to reduce nesting

#### File Structure
```typescript
// 1. Imports (external -> internal -> types)
import React from 'react';
import { useQuery } from '@tanstack/react-query';

import { apiClient } from '@/lib/api';
import { Button } from '@/components/Button';

import type { User } from '@/types/user';

// 2. Types/Interfaces
interface Props {
    userId: string;
    onUpdate?: (user: User) => void;
}

// 3. Constants
const REFRESH_INTERVAL = 5000;

// 4. Helper functions
const formatUserName = (user: User): string => {
    return `${user.firstName} ${user.lastName}`;
};

// 5. Main component/class
export function UserProfile({ userId, onUpdate }: Props) {
    // Implementation
}

// 6. Exports
export type { Props as UserProfileProps };
```

### 5. Error Handling

#### General Principles
- Use custom error classes for different error types
- Always handle Promise rejections
- Use try/catch for async operations
- Provide meaningful error messages

Example:
```typescript
class ValidationError extends Error {
    constructor(
        message: string,
        public readonly field: string
    ) {
        super(message);
        this.name = 'ValidationError';
    }
}

async function createUser(data: unknown): Promise<User> {
    try {
        const validated = validateUser(data);
        return await apiClient.post('/users', validated);
    } catch (error) {
        if (error instanceof ValidationError) {
            console.error(`Validation failed for field: ${error.field}`);
        }
        throw error;
    }
}
```

#### Null Safety
- Enable `strictNullChecks` in tsconfig.json
- Use optional chaining (`?.`) and nullish coalescing (`??`)
- Avoid non-null assertions (`!`) unless absolutely necessary

Example:
```typescript
// Good
const userName = user?.profile?.name ?? 'Anonymous';

// Avoid
const userName = user!.profile!.name; // Risky!
```

### 6. Module Template

```typescript
/**
 * @file moduleName.ts
 * @brief Brief description of the module
 */

// =============================================================================
// Imports
// =============================================================================

import { dependency } from 'library';

// =============================================================================
// Types & Interfaces
// =============================================================================

/**
 * Configuration options for the module.
 */
export interface ModuleConfig {
    /** API endpoint URL */
    apiUrl: string;
    /** Request timeout in milliseconds */
    timeout?: number;
    /** Enable debug logging */
    debug?: boolean;
}

/**
 * Result type for module operations.
 */
export type OperationResult<T> =
    | { success: true; data: T }
    | { success: false; error: string };

// =============================================================================
// Constants
// =============================================================================

const DEFAULT_TIMEOUT = 30000;

// =============================================================================
// Private Functions
// =============================================================================

/**
 * Validates the configuration object.
 *
 * @param config - The configuration to validate
 *
 * @returns True if valid, false otherwise
 */
function validateConfig(config: unknown): config is ModuleConfig {
    return (
        typeof config === 'object' &&
        config !== null &&
        'apiUrl' in config &&
        typeof (config as ModuleConfig).apiUrl === 'string'
    );
}

// =============================================================================
// Public Functions
// =============================================================================

/**
 * Creates a module instance with the given configuration.
 *
 * @param config - Configuration options
 *
 * @returns Initialized module instance
 *
 * @throws {Error} When configuration is invalid
 */
export function createModule(config: ModuleConfig): ModuleInstance {
    if (!validateConfig(config)) {
        throw new Error('Invalid configuration provided');
    }

    return {
        config,
        
        async execute<T>(operation: () => Promise<T>): Promise<OperationResult<T>> {
            try {
                const data = await operation();
                return { success: true, data };
            } catch (error) {
                return { 
                    success: false, 
                    error: error instanceof Error ? error.message : 'Unknown error' 
                };
            }
        }
    };
}

// =============================================================================
// Types - Re-export for convenience
// =============================================================================

export type { ModuleInstance } from './types';
```

### 7. Class Template

```typescript
/**
 * @file UserManager.ts
 * @brief Manages user operations and caching
 */

import type { User, UserCache } from './types';

/**
 * Manages user data with caching capabilities.
 */
export class UserManager {
    private readonly cache: Map<string, User>;
    private readonly maxCacheSize: number;

    /**
     * Creates a new UserManager instance.
     *
     * @param maxCacheSize - Maximum number of users to cache (default: 100)
     */
    constructor(maxCacheSize: number = 100) {
        this.cache = new Map();
        this.maxCacheSize = maxCacheSize;
    }

    /**
     * Retrieves a user by ID.
     *
     * @param userId - The unique user identifier
     *
     * @returns The user object or null if not found
     */
    getUser(userId: string): User | null {
        return this.cache.get(userId) ?? null;
    }

    /**
     * Adds or updates a user in the cache.
     *
     * @param user - The user to cache
     */
    setUser(user: User): void {
        if (this.cache.size >= this.maxCacheSize) {
            // Remove oldest entry
            const firstKey = this.cache.keys().next().value;
            this.cache.delete(firstKey);
        }
        
        this.cache.set(user.id, user);
    }

    /**
     * Clears all cached users.
     */
    clear(): void {
        this.cache.clear();
    }

    /**
     * Gets the current cache size.
     *
     * @returns Number of cached users
     */
    get size(): number {
        return this.cache.size;
    }
}
```

### 8. React Component Template

```typescript
/**
 * @file UserProfile.tsx
 * @brief User profile display component
 */

import React, { useCallback, useEffect, useState } from 'react';

import { fetchUser } from '@/api/user';
import { Avatar } from '@/components/Avatar';
import { Button } from '@/components/Button';

import type { User } from '@/types/user';

// =============================================================================
// Types
// =============================================================================

interface UserProfileProps {
    /** User ID to display */
    userId: string;
    /** Called when profile is updated */
    onUpdate?: (user: User) => void;
    /** Show edit button */
    editable?: boolean;
}

// =============================================================================
// Component
// =============================================================================

/**
 * Displays user profile information with optional editing.
 */
export const UserProfile: React.FC<UserProfileProps> = ({
    userId,
    onUpdate,
    editable = false
}) => {
    const [user, setUser] = useState<User | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const loadUser = useCallback(async () => {
        setLoading(true);
        setError(null);
        
        try {
            const data = await fetchUser(userId);
            setUser(data);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load user');
        } finally {
            setLoading(false);
        }
    }, [userId]);

    useEffect(() => {
        loadUser();
    }, [loadUser]);

    const handleEdit = useCallback(() => {
        if (user && onUpdate) {
            onUpdate(user);
        }
    }, [user, onUpdate]);

    if (loading) {
        return <div>Loading...</div>;
    }

    if (error) {
        return <div role="alert">Error: {error}</div>;
    }

    if (!user) {
        return <div>User not found</div>;
    }

    return (
        <div className="user-profile">
            <Avatar src={user.avatarUrl} alt={user.name} />
            <h2>{user.name}</h2>
            <p>{user.email}</p>
            
            {editable && (
                <Button onClick={handleEdit}>
                    Edit Profile
                </Button>
            )}
        </div>
    );
};

// =============================================================================
// Exports
// =============================================================================

export type { UserProfileProps };
export default UserProfile;
```

### 9. Common Patterns

#### Factory Pattern
```typescript
interface Animal {
    speak(): string;
}

class Dog implements Animal {
    speak() {
        return 'Woof!';
    }
}

class Cat implements Animal {
    speak() {
        return 'Meow!';
    }
}

class AnimalFactory {
    static create(type: 'dog' | 'cat'): Animal {
        switch (type) {
            case 'dog':
                return new Dog();
            case 'cat':
                return new Cat();
            default:
                throw new Error(`Unknown animal type: ${type}`);
        }
    }
}
```

#### Builder Pattern
```typescript
class QueryBuilder<T> {
    private conditions: string[] = [];
    private orderByField?: string;
    private limitValue?: number;

    where(condition: string): this {
        this.conditions.push(condition);
        return this;
    }

    orderBy(field: string): this {
        this.orderByField = field;
        return this;
    }

    limit(count: number): this {
        this.limitValue = count;
        return this;
    }

    build(): string {
        let query = 'SELECT * FROM table';
        
        if (this.conditions.length > 0) {
            query += ` WHERE ${this.conditions.join(' AND ')}`;
        }
        
        if (this.orderByField) {
            query += ` ORDER BY ${this.orderByField}`;
        }
        
        if (this.limitValue) {
            query += ` LIMIT ${this.limitValue}`;
        }
        
        return query;
    }
}
```

#### Repository Pattern
```typescript
interface Repository<T, ID> {
    findById(id: ID): Promise<T | null>;
    findAll(): Promise<T[]>;
    save(entity: T): Promise<T>;
    delete(id: ID): Promise<void>;
}

class UserRepository implements Repository<User, string> {
    async findById(id: string): Promise<User | null> {
        // Implementation
        return null;
    }

    async findAll(): Promise<User[]> {
        // Implementation
        return [];
    }

    async save(user: User): Promise<User> {
        // Implementation
        return user;
    }

    async delete(id: string): Promise<void> {
        // Implementation
    }
}
```

### 10. Code Quality Verification

#### Static Analysis Tools
- **ESLint**: Linting and style enforcement
  ```bash
  npx eslint src/
  ```
- **TypeScript Compiler**: Type checking
  ```bash
  npx tsc --noEmit
  ```
- **Prettier**: Code formatting
  ```bash
  npx prettier --check src/
  ```

#### Configuration Example (tsconfig.json)
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "declaration": true,
    "declarationMap": true,
    "sourceMap": true,
    "outDir": "./dist",
    "rootDir": "./src",
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist"]
}
```

## Summary

This skill provides TypeScript coding best practices:
- **Type system**: Proper use of interfaces, types, and type guards
- **Memory management**: Resource cleanup and weak references
- **Code style**: TAB indentation, camelCase/PascalCase naming
- **Documentation**: JSDoc with @ parameters
- **Templates**: Module, class, and React component patterns
- **Quality**: ESLint, TypeScript compiler, Prettier

For project-specific APIs and conventions, refer to project documentation.