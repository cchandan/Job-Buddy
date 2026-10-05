# Job Buddy
<!-- impeccable:product-schema 1 -->

## Platform
web

## Users and purpose
A private workspace for a family finding and applying for jobs together. Job seekers manage their own applications; admins share jobs, create accounts and review CVs. Existing product facts are documented in Plan/spec.md and README.md.

## Capabilities and constraints
FastAPI and Jinja, with no separate frontend framework. Job Board, Tracker, Calendar, core CV, AI-assisted tailored CVs and admin review. All personal data requires sign-in. The public landing page explains the workflow using illustrative content only. No public sign-up, payment plans or invented customer claims.

## Deployment
Render. Supabase for durable data; SQLite is explicitly supported for the owner's temporary deployment test. First-admin credentials can be provided through Render environment variables, with no shell required. Existing accounts must never be reset or elevated by bootstrap.

## Brand
Job Buddy. Plain, friendly language. Existing Archivo typography and cobalt/lime palette provide the visual identity; the owner requested an exceptionally polished public landing page using Impeccable.
