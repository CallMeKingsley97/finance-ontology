.PHONY: install seed dev build health clean

install:
	pnpm install
	cd backend && uv sync

seed:
	pnpm db:seed

dev:
	pnpm dev

build:
	pnpm build

health:
	pnpm health

clean:
	pnpm clean
