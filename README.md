# TokenConfig

## Important

Use Github API to get latest configurations to avoid CDN caching issues. See the api document [here](https://docs.github.com/en/rest/repos/contents?apiVersion=2022-11-28).

For example:

```
https://api.github.com/repos/HertzFlow/TokenConfig/contents/oracle/cex.local.json?ref=kayce/test
```

Note that the public api has a strict rate limit (60 times per hour), you need to configure your own `GithubAccessToken` to increase the limit to 5000 times per hour.
See [here](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api#primary-rate-limit-for-authenticated-users).

## Go Demo

```go
const URL = "https://api.github.com/repos/HertzFlow/TokenConfig/contents/oracle/cex.local.json?ref=kayce/test"
const GITHUB_ACCESS_TOKEN = "Your access token"

func fetch() (*model.RemoteTokenConfig, error) {
	req, _ := http.NewRequest("GET", URL, nil)
	req.Header.Add("accept", "application/vnd.github+json")
	req.Header.Add("Authorization", "Bearer " + GITHUB_ACCESS_TOKEN)
	req.Header.Add("X-GitHub-Api-Version", "2022-11-28")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	type GitHubContent struct {
		Content  string `json:"content"`
		Encoding string `json:"encoding"`
	}
	var content GitHubContent
	if err := json.NewDecoder(resp.Body).Decode(&content); err != nil {
		return nil, err
	}

	body, err := base64.StdEncoding.DecodeString(content.Content)
	if err != nil {
		return nil, err
	}

	var cfg *model.RemoteTokenConfig
	if err := sonic.Unmarshal(body, &cfg); err != nil {
		return nil, err
	}
	return cfg, nil
}
```
