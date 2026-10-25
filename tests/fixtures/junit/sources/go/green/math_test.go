package math

import "testing"

func TestAdds(t *testing.T) {
	if 1+1 != 2 {
		t.Fatal("1 + 1 != 2")
	}
}

func TestSubtracts(t *testing.T) {
	if 2-1 != 1 {
		t.Fatal("2 - 1 != 1")
	}
}

func TestSkips(t *testing.T) {
	t.Skip("not now")
}
