
        # Perform research
        print("Researching company...")
        print()

        result = researcher.perform_research(company_name, domain)

        # Handle results
        if "error" in result:
            print()
            print("=" * 70)
            print("❌ RESEARCH FAILED")
            print("=" * 70)
            print()
            print(f"Error: {result['error']}")
            print()
        else:
            # Save results
            print()
            print("=" * 70)
            print("✅ RESEARCH COMPLETED")
            print("=" * 70)
            print()

            file_path = researcher.save_results(result, company_name)

            # Show summary
            print()
            print("SUMMARY:")
            print(f"  Company: {result.get('company_name', 'N/A')}")
            print(f"  Domain: {result.get('domain', 'N/A')}")
            print(f"  Industry: {result.get('industry_and_segment', 'N/A')}")
            print(f"  Founded: {result.get('year_founded', 'N/A')}")
            print(f"  Funding: {result.get('funding_raised', 'N/A')}")
            if file_path:
                print(f"  File: {file_path}")
            print()

    except KeyboardInterrupt:
        print()
        print()
        print("❌ Research interrupted by user")
        print()

    except Exception as error:
        print()
        print("=" * 70)
        print("❌ UNEXPECTED ERROR")
        print("=" * 70)
        print()
        print(f"Error: {str(error)}")
        print()


if __name__ == "__main__":
    main()