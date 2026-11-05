# Build before measurement; runtime never invokes Go or downloads dependencies.
GO ?= go
export GOTOOLCHAIN = local
export CGO_ENABLED = 0

.PHONY: all test verify clean
all: broker

broker: main.go go.mod go.sum
	$(GO) build -mod=readonly -buildvcs=false -trimpath -o $@ .

test:
	$(GO) test -mod=readonly ./...

verify:
	$(GO) mod verify

clean:
	rm -f broker
